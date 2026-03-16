<script lang="ts">
  /**
   * UploadForm.svelte
   *
   * Interactive Svelte island for the upload page.
   *
   * Two auth methods:
   * A) OAuth Login — "Login with GitHub" button → GitHub OAuth redirect →
   *    GitHub Actions exchanges code for token → browser polls + decrypts.
   * B) PAT Fallback — paste a Personal Access Token directly.
   *
   * Once authenticated, the user can upload a PDF and trigger translation.
   */

  import { onMount } from 'svelte';

  // --- Configuration ---
  // OAUTH_CLIENT_ID is public (safe to commit — it's how OAuth works).
  // TRIGGER_TOKEN is a minimal-scope PAT used to trigger the exchange workflow.
  // Set as PUBLIC_TRIGGER_TOKEN repo variable → injected at build time by deploy.yml.
  const OAUTH_CLIENT_ID = 'Ov23liyIIo3KzEvudut4';
  const TRIGGER_TOKEN = import.meta.env.PUBLIC_TRIGGER_TOKEN || '';
  const DEFAULT_REPO_OWNER = 'shahriarspace';
  const DEFAULT_REPO_NAME = 'bangla-pdf-translator';

  const STORAGE_KEY = 'bangla-translator-github';
  const OAUTH_STATE_KEY = 'bangla-translator-oauth-state';
  const PAT_CREATE_URL = 'https://github.com/settings/tokens/new?scopes=repo,workflow&description=Bangla+PDF+Translator';
  const REDIRECT_URI = typeof window !== 'undefined'
    ? `${window.location.origin}${window.location.pathname}`
    : '';

  // --- State ---
  let file: File | null = $state(null);
  let dragOver = $state(false);
  let status: 'idle' | 'uploading' | 'submitted' | 'error' = $state('idle');
  let errorMsg = $state('');
  let submittedSlug = $state('');

  // Translation options
  let translationMode = $state('offline');
  let ocrEngine = $state('tesseract');
  let refinementProvider = $state('none');
  let refinementModel = $state('gpt-4o-mini');
  let aiProvider = $state('github');
  let aiOcrModel = $state('gpt-4o');
  let aiTranslateModel = $state('gpt-4o');

  // Book metadata (optional)
  let bookTitle = $state('');
  let bookAuthor = $state('');

  // Auth state
  let githubToken = $state('');
  let githubUser = $state('');
  let githubAvatar = $state('');
  let authStatus: 'disconnected' | 'oauth-pending' | 'connecting' | 'connected' | 'error' = $state('disconnected');
  let authError = $state('');
  let authMethod: 'oauth' | 'pat' = $state('oauth');
  let oauthProgress = $state('');
  let tokenInput = $state('');

  // Repo config
  let repoOwner = $state(DEFAULT_REPO_OWNER);
  let repoName = $state(DEFAULT_REPO_NAME);

  const models = [
    { value: 'gpt-4o-mini', label: 'GPT-4o Mini (default)' },
    { value: 'gpt-4o', label: 'GPT-4o (best quality)' },
    { value: 'o3-mini', label: 'o3-mini' },
    { value: 'DeepSeek-R1', label: 'DeepSeek R1' },
    { value: 'Mistral-Large-2411', label: 'Mistral Large' },
    { value: 'Meta-Llama-3.1-405B-Instruct', label: 'Llama 3.1 405B' },
  ];

  const aiModels = [
    { value: 'gpt-4o', label: 'GPT-4o (recommended)' },
    { value: 'gpt-4o-mini', label: 'GPT-4o Mini (faster, cheaper)' },
    { value: 'o3-mini', label: 'o3-mini' },
    { value: 'DeepSeek-R1', label: 'DeepSeek R1' },
  ];

  const aiVisionModels = [
    { value: 'gpt-4o', label: 'GPT-4o (recommended)' },
    { value: 'gpt-4o-mini', label: 'GPT-4o Mini (faster, cheaper)' },
  ];

  let needsAiSettings = $derived(ocrEngine === 'ai' || translationMode === 'ai');
  let oauthAvailable = $derived(TRIGGER_TOKEN !== '');

  // --- Lifecycle ---

  onMount(() => {
    // 1. Restore saved session
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const data = JSON.parse(saved);
        if (data.token && data.user) {
          githubToken = data.token;
          githubUser = data.user;
          githubAvatar = data.avatar || '';
          repoOwner = data.repoOwner || DEFAULT_REPO_OWNER;
          repoName = data.repoName || DEFAULT_REPO_NAME;
          authStatus = 'connected';
          return; // Already logged in, skip OAuth check
        }
      }
    } catch {
      // ignore
    }

    // 2. Check for OAuth callback (?code=XXX&state=YYY)
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const returnedState = params.get('state');

    if (code && returnedState) {
      // Clean the URL (remove ?code=...&state=... from address bar)
      const cleanUrl = window.location.pathname;
      window.history.replaceState({}, '', cleanUrl);

      // Verify state matches what we stored
      const savedState = sessionStorage.getItem(OAUTH_STATE_KEY);
      if (savedState === returnedState) {
        handleOAuthCallback(code, returnedState);
      } else {
        authStatus = 'error';
        authError = 'OAuth state mismatch. Please try logging in again.';
      }
    }
  });

  // --- Auth functions ---

  function saveSession() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        token: githubToken,
        user: githubUser,
        avatar: githubAvatar,
        repoOwner,
        repoName,
      }));
    } catch {
      // localStorage might be unavailable
    }
  }

  function generateState(): string {
    const array = new Uint8Array(32);
    crypto.getRandomValues(array);
    return Array.from(array, b => b.toString(16).padStart(2, '0')).join('');
  }

  function startOAuthLogin() {
    const state = generateState();
    sessionStorage.setItem(OAUTH_STATE_KEY, state);

    const authUrl = new URL('https://github.com/login/oauth/authorize');
    authUrl.searchParams.set('client_id', OAUTH_CLIENT_ID);
    authUrl.searchParams.set('redirect_uri', REDIRECT_URI);
    authUrl.searchParams.set('scope', 'repo workflow');
    authUrl.searchParams.set('state', state);

    window.location.href = authUrl.toString();
  }

  async function handleOAuthCallback(code: string, state: string) {
    authStatus = 'oauth-pending';
    authError = '';
    oauthProgress = 'Triggering token exchange...';

    const triggerToken = TRIGGER_TOKEN;

    try {
      // Step 1: Trigger the oauth-exchange workflow
      oauthProgress = 'Starting secure token exchange...';
      const dispatchRes = await fetch(
        `https://api.github.com/repos/${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME}/actions/workflows/oauth-exchange.yml/dispatches`,
        {
          method: 'POST',
          headers: {
            Authorization: `token ${triggerToken}`,
            'Content-Type': 'application/json',
            Accept: 'application/vnd.github.v3+json',
          },
          body: JSON.stringify({
            ref: 'main',
            inputs: { code, state },
          }),
        }
      );

      if (!dispatchRes.ok) {
        const text = await dispatchRes.text();
        throw new Error(`Failed to trigger exchange: ${dispatchRes.status} ${text}`);
      }

      // Step 2: Poll for the workflow run
      oauthProgress = 'Waiting for GitHub Actions to process...';
      await new Promise(r => setTimeout(r, 3000)); // Initial delay

      const token = await pollForOAuthResult(state, triggerToken);

      if (!token) {
        throw new Error('Token exchange timed out. Please try again.');
      }

      // Step 3: Validate the token and get user info
      oauthProgress = 'Verifying your identity...';
      const userRes = await fetch('https://api.github.com/user', {
        headers: {
          Authorization: `token ${token}`,
          Accept: 'application/vnd.github.v3+json',
        },
      });

      if (!userRes.ok) {
        throw new Error('Failed to verify token. Please try again.');
      }

      const user = await userRes.json();
      githubToken = token;
      githubUser = user.login;
      githubAvatar = user.avatar_url || '';
      repoOwner = repoOwner || user.login;
      authStatus = 'connected';
      oauthProgress = '';
      saveSession();

    } catch (e: any) {
      authStatus = 'error';
      authError = e.message || 'OAuth failed';
      oauthProgress = '';
    } finally {
      sessionStorage.removeItem(OAUTH_STATE_KEY);
    }
  }

  async function pollForOAuthResult(state: string, triggerToken: string): Promise<string | null> {
    const maxAttempts = 30; // 30 * 3s = 90s max
    const pollInterval = 3000;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      oauthProgress = `Waiting for token exchange... (${attempt + 1}/${maxAttempts})`;

      try {
        // Find the workflow run
        const runsRes = await fetch(
          `https://api.github.com/repos/${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME}/actions/workflows/oauth-exchange.yml/runs?per_page=5`,
          {
            headers: {
              Authorization: `token ${triggerToken}`,
              Accept: 'application/vnd.github.v3+json',
            },
          }
        );

        if (!runsRes.ok) continue;
        const runsData = await runsRes.json();

        // Find a completed run (look at recent runs)
        for (const run of runsData.workflow_runs || []) {
          if (run.status === 'completed') {
            // Check artifacts for our state
            const artifactsRes = await fetch(
              `https://api.github.com/repos/${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME}/actions/runs/${run.id}/artifacts`,
              {
                headers: {
                  Authorization: `token ${triggerToken}`,
                  Accept: 'application/vnd.github.v3+json',
                },
              }
            );

            if (!artifactsRes.ok) continue;
            const artifactsData = await artifactsRes.json();

            const artifact = (artifactsData.artifacts || []).find(
              (a: any) => a.name === `oauth-result-${state}`
            );

            if (artifact) {
              oauthProgress = 'Downloading and decrypting token...';

              // Download the artifact (zip file)
              const downloadRes = await fetch(
                `https://api.github.com/repos/${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME}/actions/artifacts/${artifact.id}/zip`,
                {
                  headers: {
                    Authorization: `token ${triggerToken}`,
                    Accept: 'application/vnd.github.v3+json',
                  },
                }
              );

              if (!downloadRes.ok) continue;

              // The artifact is a zip; we need to extract result.json
              const blob = await downloadRes.blob();
              const zip = await unzipBlob(blob);
              const resultJson = JSON.parse(zip);

              if (resultJson.error) {
                throw new Error(`GitHub OAuth error: ${resultJson.error}: ${resultJson.error_description}`);
              }

              if (resultJson.encrypted_token) {
                // XOR-decrypt with state
                const encrypted = atob(resultJson.encrypted_token);
                const key = state.repeat(Math.ceil(encrypted.length / state.length)).slice(0, encrypted.length);
                let token = '';
                for (let i = 0; i < encrypted.length; i++) {
                  token += String.fromCharCode(encrypted.charCodeAt(i) ^ key.charCodeAt(i));
                }
                return token;
              }
            }
          }
        }
      } catch (e: any) {
        if (e.message.includes('GitHub OAuth error')) throw e;
        // Network errors — keep polling
      }

      await new Promise(r => setTimeout(r, pollInterval));
    }

    return null;
  }

  async function unzipBlob(blob: Blob): Promise<string> {
    // Parse zip and extract the first file.
    // GitHub artifact zips contain a single file: result.json
    // We use the End-of-Central-Directory + Central Directory to get reliable sizes,
    // because the local file header may have sizes set to 0 (data descriptor flag).

    const buffer = await blob.arrayBuffer();
    const view = new DataView(buffer);
    const bytes = new Uint8Array(buffer);

    // Step 1: Find End of Central Directory record (scan from end)
    // EOCD signature = PK\x05\x06 = 0x06054b50
    let eocdOffset = -1;
    for (let i = bytes.length - 22; i >= 0; i--) {
      if (view.getUint32(i, true) === 0x06054b50) {
        eocdOffset = i;
        break;
      }
    }

    let compressedSize = 0;
    let compressionMethod = 0;
    let localHeaderOffset = 0;

    if (eocdOffset >= 0) {
      // Parse EOCD to find central directory
      const cdOffset = view.getUint32(eocdOffset + 16, true);

      // Parse first Central Directory entry (PK\x01\x02 = 0x02014b50)
      if (view.getUint32(cdOffset, true) === 0x02014b50) {
        compressionMethod = view.getUint16(cdOffset + 10, true);
        compressedSize = view.getUint32(cdOffset + 20, true);
        localHeaderOffset = view.getUint32(cdOffset + 42, true);
      }
    }

    // Step 2: Parse local file header to find data start
    if (localHeaderOffset === 0) {
      // Fallback: find first local header
      for (let i = 0; i < bytes.length - 4; i++) {
        if (view.getUint32(i, true) === 0x04034b50) {
          localHeaderOffset = i;
          break;
        }
      }
    }

    const lhCompressionMethod = view.getUint16(localHeaderOffset + 8, true);
    const lhCompressedSize = view.getUint32(localHeaderOffset + 18, true);
    const fileNameLen = view.getUint16(localHeaderOffset + 26, true);
    const extraLen = view.getUint16(localHeaderOffset + 28, true);
    const dataOffset = localHeaderOffset + 30 + fileNameLen + extraLen;

    // Use central directory sizes if local header has 0 (data descriptor)
    const method = compressionMethod || lhCompressionMethod;
    const dataSize = compressedSize || lhCompressedSize;

    if (method === 0) {
      // Stored (no compression)
      return new TextDecoder().decode(buffer.slice(dataOffset, dataOffset + dataSize));
    } else if (method === 8) {
      // Deflate — if we don't have a reliable size, slice to next PK signature or end
      let endOffset = dataOffset + dataSize;
      if (dataSize === 0) {
        // Find next PK signature (data descriptor or central directory)
        for (let i = dataOffset; i < bytes.length - 4; i++) {
          const sig = view.getUint32(i, true);
          if (sig === 0x08074b50 || sig === 0x02014b50) { // data descriptor or central dir
            endOffset = i;
            break;
          }
        }
        if (endOffset === dataOffset) endOffset = bytes.length;
      }

      const compressedData = buffer.slice(dataOffset, endOffset);
      const ds = new DecompressionStream('deflate-raw');
      const writer = ds.writable.getWriter();
      const reader = ds.readable.getReader();

      writer.write(new Uint8Array(compressedData));
      writer.close();

      const chunks: Uint8Array[] = [];
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
      }

      const totalLen = chunks.reduce((acc, c) => acc + c.length, 0);
      const result = new Uint8Array(totalLen);
      let pos = 0;
      for (const chunk of chunks) {
        result.set(chunk, pos);
        pos += chunk.length;
      }
      return new TextDecoder().decode(result);
    }

    throw new Error('Unsupported zip compression method');
  }

  async function connectWithPAT() {
    if (!tokenInput.trim()) return;

    authStatus = 'connecting';
    authError = '';

    try {
      const res = await fetch('https://api.github.com/user', {
        headers: {
          Authorization: `token ${tokenInput.trim()}`,
          Accept: 'application/vnd.github.v3+json',
        },
      });

      if (!res.ok) {
        if (res.status === 401) throw new Error('Invalid token. Please check and try again.');
        throw new Error(`GitHub API error: ${res.status}`);
      }

      const user = await res.json();
      githubToken = tokenInput.trim();
      githubUser = user.login;
      githubAvatar = user.avatar_url || '';
      repoOwner = repoOwner || user.login;
      authStatus = 'connected';
      tokenInput = '';
      saveSession();
    } catch (e: any) {
      authStatus = 'error';
      authError = e.message || 'Failed to connect';
    }
  }

  function disconnect() {
    githubToken = '';
    githubUser = '';
    githubAvatar = '';
    tokenInput = '';
    authStatus = 'disconnected';
    authError = '';
    oauthProgress = '';
    try {
      localStorage.removeItem(STORAGE_KEY);
      sessionStorage.removeItem(OAUTH_STATE_KEY);
    } catch {
      // ignore
    }
  }

  // --- Form functions ---

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    dragOver = false;
    const f = e.dataTransfer?.files?.[0];
    if (f?.name.toLowerCase().endsWith('.pdf')) {
      file = f;
    }
  }

  function handleFileInput(e: Event) {
    const input = e.target as HTMLInputElement;
    if (input.files?.[0]) {
      file = input.files[0];
    }
  }

  function slugify(name: string): string {
    return name
      .replace(/\.pdf$/i, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');
  }

  async function handleSubmit() {
    if (!file || !githubToken || !repoOwner) return;

    status = 'uploading';
    errorMsg = '';

    const slug = slugify(file.name);
    submittedSlug = slug;

    try {
      // Step 1: Read file as base64
      const base64Content = await fileToBase64(file);

      // Step 2: Upload PDF to repo via Contents API (CORS-compatible)
      // Puts file at uploads/{slug}/{filename}
      const filePath = `uploads/${slug}/${file.name}`;
      const uploadRes = await fetch(
        `https://api.github.com/repos/${repoOwner}/${repoName}/contents/${encodeURIComponent(filePath)}`,
        {
          method: 'PUT',
          headers: {
            Authorization: `token ${githubToken}`,
            'Content-Type': 'application/json',
            Accept: 'application/vnd.github.v3+json',
          },
          body: JSON.stringify({
            message: `Upload PDF for translation: ${file.name}`,
            content: base64Content,
            branch: 'main',
          }),
        }
      );

      if (!uploadRes.ok) {
        const errText = await uploadRes.text();
        throw new Error(`Failed to upload PDF: ${uploadRes.status} ${errText}`);
      }

      // Step 3: Trigger the translation workflow
      const dispatchRes = await fetch(
        `https://api.github.com/repos/${repoOwner}/${repoName}/actions/workflows/translate.yml/dispatches`,
        {
          method: 'POST',
          headers: {
            Authorization: `token ${githubToken}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            ref: 'main',
            inputs: {
              pdf_filename: file.name,
              slug: slug,
              pdf_path: filePath,
              translation_mode: translationMode,
              ocr_engine: ocrEngine,
              refinement_provider: refinementProvider,
              refinement_model: refinementProvider !== 'none' ? refinementModel : '',
              ai_provider: needsAiSettings ? aiProvider : 'github',
              ai_ocr_model: ocrEngine === 'ai' ? aiOcrModel : 'gpt-4o',
              ai_translate_model: translationMode === 'ai' ? aiTranslateModel : 'gpt-4o',
              book_title: bookTitle.trim(),
              book_author: bookAuthor.trim(),
            },
          }),
        }
      );

      if (!dispatchRes.ok) {
        throw new Error(`Failed to trigger workflow: ${dispatchRes.status} ${await dispatchRes.text()}`);
      }

      status = 'submitted';
    } catch (e: any) {
      status = 'error';
      errorMsg = e.message || 'Unknown error';
    }
  }

  function fileToBase64(f: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        // Remove the data:...;base64, prefix
        const result = (reader.result as string).split(',')[1];
        resolve(result);
      };
      reader.onerror = reject;
      reader.readAsDataURL(f);
    });
  }
</script>

<div class="upload-form">
  {#if status === 'submitted'}
    <div class="success-panel">
      <div class="success-icon">&#10003;</div>
      <h2>Translation Submitted</h2>
      <p>Your PDF has been uploaded and the translation workflow has been triggered.</p>
      <p class="muted">The GitHub Action is now processing your book. This may take several minutes depending on the number of pages.</p>
      <div class="success-actions">
        <a
          href={`https://github.com/${repoOwner}/${repoName}/actions`}
          target="_blank"
          rel="noopener"
          class="btn btn-primary"
        >
          View Workflow Progress
        </a>
        <button class="btn btn-outline" onclick={() => { status = 'idle'; file = null; }}>
          Upload Another
        </button>
      </div>
    </div>

  {:else}
    <!-- GitHub Auth Section -->
    <div class="config-section card">
      {#if authStatus === 'connected'}
        <!-- Connected state -->
        <div class="auth-connected">
          <div class="auth-user">
            {#if githubAvatar}
              <img src={githubAvatar} alt={githubUser} class="auth-avatar" />
            {:else}
              <div class="auth-avatar-placeholder">&#128100;</div>
            {/if}
            <div class="auth-info">
              <span class="auth-name">{githubUser}</span>
              <span class="auth-status-text">Connected to GitHub</span>
            </div>
            <button class="btn btn-sm btn-outline" onclick={disconnect}>Disconnect</button>
          </div>
          <div class="form-row" style="margin-top: 0.8rem;">
            <label>
              <span>Repository Owner</span>
              <input type="text" bind:value={repoOwner} placeholder={githubUser} onchange={saveSession} />
            </label>
            <label>
              <span>Repository Name</span>
              <input type="text" bind:value={repoName} placeholder="bangla-pdf-translator" onchange={saveSession} />
            </label>
          </div>
        </div>

      {:else if authStatus === 'oauth-pending'}
        <!-- OAuth in progress -->
        <div class="oauth-pending">
          <div class="spinner"></div>
          <h3>Logging in...</h3>
          <p class="help-text">{oauthProgress}</p>
        </div>

      {:else}
        <!-- Login options -->
        <h3>Connect to GitHub</h3>

        {#if oauthAvailable}
          <!-- Primary: OAuth Login -->
          <button class="btn btn-github login-btn" onclick={startOAuthLogin}>
            <svg class="github-icon" viewBox="0 0 16 16" width="20" height="20" fill="currentColor">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
            </svg>
            Login with GitHub
          </button>
          <p class="help-text" style="text-align: center; margin-top: 0.5rem;">
            Securely authenticate via GitHub OAuth
          </p>

          <div class="auth-divider">
            <span>or</span>
          </div>
        {/if}

        <!-- Fallback: PAT -->
        <div class="pat-section" class:collapsed={oauthAvailable && authMethod !== 'pat'}>
          {#if oauthAvailable}
            <button
              class="pat-toggle"
              onclick={() => authMethod = authMethod === 'pat' ? 'oauth' : 'pat'}
            >
              {authMethod === 'pat' ? 'Hide' : 'Use'} Personal Access Token instead
            </button>
          {/if}

          {#if !oauthAvailable || authMethod === 'pat'}
            <div class="auth-steps">
              <div class="auth-step">
                <span class="step-number">1</span>
                <div class="step-content">
                  <a href={PAT_CREATE_URL} target="_blank" rel="noopener" class="btn btn-sm btn-primary">
                    Create a Token on GitHub
                  </a>
                  <span class="step-hint">Opens GitHub with the right permissions pre-selected</span>
                </div>
              </div>
              <div class="auth-step">
                <span class="step-number">2</span>
                <div class="step-content">
                  <div class="token-input-row">
                    <input
                      type="password"
                      bind:value={tokenInput}
                      placeholder="Paste your token here (ghp_...)"
                      onkeydown={(e) => { if (e.key === 'Enter') connectWithPAT(); }}
                      disabled={authStatus === 'connecting'}
                    />
                    <button
                      class="btn btn-green"
                      onclick={connectWithPAT}
                      disabled={!tokenInput.trim() || authStatus === 'connecting'}
                    >
                      {authStatus === 'connecting' ? 'Connecting...' : 'Connect'}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          {/if}
        </div>

        {#if authStatus === 'error'}
          <div class="error-msg" style="margin-top: 0.8rem;">{authError}</div>
        {/if}

        <p class="help-text" style="margin-top: 1rem; font-size: 0.8rem;">
          Your credentials are stored locally in your browser and never sent to any server except GitHub's API.
        </p>
      {/if}
    </div>

    <!-- Drop Zone -->
    <div
      class="drop-zone"
      class:drag-over={dragOver}
      class:has-file={file !== null}
      role="button"
      tabindex="0"
      ondragover={(e) => { e.preventDefault(); dragOver = true; }}
      ondragleave={() => dragOver = false}
      ondrop={handleDrop}
      onclick={() => document.getElementById('file-input')?.click()}
      onkeydown={(e) => { if (e.key === 'Enter') document.getElementById('file-input')?.click(); }}
    >
      <input
        id="file-input"
        type="file"
        accept=".pdf"
        onchange={handleFileInput}
        hidden
      />
      {#if file}
        <div class="file-info">
          <span class="file-icon">&#128196;</span>
          <span class="file-name">{file.name}</span>
          <span class="file-size">({(file.size / 1024 / 1024).toFixed(1)} MB)</span>
        </div>
      {:else}
        <div class="drop-prompt">
          <span class="drop-icon">&#128228;</span>
          <span>Drop a Bangla PDF here or click to browse</span>
        </div>
      {/if}
    </div>

    <!-- Translation Options -->
    <div class="options-section card">
      <h3>Book Details (optional)</h3>
      <p class="help-text">If left blank, the title and author will be extracted from the PDF metadata or derived from the filename.</p>
      <div class="form-row">
        <label>
          <span>Book Title</span>
          <input type="text" bind:value={bookTitle} placeholder="e.g. Moyurakkhi" />
        </label>
        <label>
          <span>Author</span>
          <input type="text" bind:value={bookAuthor} placeholder="e.g. Humayun Ahmed" />
        </label>
      </div>
    </div>

    <div class="options-section card">
      <h3>OCR Engine</h3>
      <div class="option-group">
        <label class="option-label">Text Extraction Method</label>
        <div class="toggle-group">
          <button class="toggle-btn" class:active={ocrEngine === 'tesseract'} onclick={() => ocrEngine = 'tesseract'}>
            Tesseract (offline)
          </button>
          <button class="toggle-btn" class:active={ocrEngine === 'ai'} onclick={() => ocrEngine = 'ai'}>
            AI Vision (online)
          </button>
        </div>
      </div>
    </div>

    <div class="options-section card">
      <h3>Translation Options</h3>
      <div class="option-group">
        <label class="option-label">Translation Engine</label>
        <div class="toggle-group">
          <button class="toggle-btn" class:active={translationMode === 'offline'} onclick={() => translationMode = 'offline'}>
            Offline (Argos)
          </button>
          <button class="toggle-btn" class:active={translationMode === 'online'} onclick={() => translationMode = 'online'}>
            Online (Google)
          </button>
          <button class="toggle-btn" class:active={translationMode === 'ai'} onclick={() => translationMode = 'ai'}>
            AI (literary)
          </button>
        </div>
      </div>

      {#if translationMode !== 'ai'}
        <div class="option-group">
          <label class="option-label">AI Refinement</label>
          <select bind:value={refinementProvider} class="select">
            <option value="none">None</option>
            <option value="github">GitHub Models</option>
            <option value="openai">OpenAI</option>
          </select>
        </div>
        {#if refinementProvider !== 'none'}
          <div class="option-group">
            <label class="option-label">Model</label>
            <select bind:value={refinementModel} class="select">
              {#each models as m}
                <option value={m.value}>{m.label}</option>
              {/each}
            </select>
          </div>
        {/if}
      {/if}
    </div>

    {#if needsAiSettings}
      <div class="options-section card">
        <h3>AI Pipeline Settings</h3>
        <div class="option-group">
          <label class="option-label">AI Provider</label>
          <select bind:value={aiProvider} class="select">
            <option value="github">GitHub Models (free with PAT)</option>
            <option value="openai">OpenAI</option>
          </select>
        </div>
        {#if ocrEngine === 'ai'}
          <div class="option-group">
            <label class="option-label">AI OCR Model (vision-capable)</label>
            <select bind:value={aiOcrModel} class="select">
              {#each aiVisionModels as m}
                <option value={m.value}>{m.label}</option>
              {/each}
            </select>
          </div>
        {/if}
        {#if translationMode === 'ai'}
          <div class="option-group">
            <label class="option-label">AI Translation Model</label>
            <select bind:value={aiTranslateModel} class="select">
              {#each aiModels as m}
                <option value={m.value}>{m.label}</option>
              {/each}
            </select>
          </div>
        {/if}
      </div>
    {/if}

    <!-- Submit -->
    {#if status === 'error'}
      <div class="error-msg">{errorMsg}</div>
    {/if}

    <button
      class="btn btn-green submit-btn"
      disabled={!file || authStatus !== 'connected' || !repoOwner || status === 'uploading'}
      onclick={handleSubmit}
    >
      {#if status === 'uploading'}
        Uploading & Triggering...
      {:else}
        Upload & Translate
      {/if}
    </button>
  {/if}
</div>

<style>
  .upload-form {
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
  }

  /* Config section */
  .config-section h3 {
    margin-bottom: 0.5rem;
  }
  .help-text {
    color: var(--text-muted);
    font-size: 0.85rem;
    margin-bottom: 1rem;
  }
  .help-text code {
    background: rgba(108, 99, 255, 0.15);
    padding: 0.15rem 0.4rem;
    border-radius: 4px;
    font-size: 0.8rem;
  }

  /* GitHub Login Button */
  .btn-github {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.6rem;
    width: 100%;
    padding: 0.75rem 1.2rem;
    background: #24292f;
    color: white;
    border: none;
    border-radius: var(--radius-sm);
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .btn-github:hover {
    background: #32383f;
  }
  .github-icon {
    flex-shrink: 0;
  }

  /* Auth divider */
  .auth-divider {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin: 1rem 0 0.5rem;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  .auth-divider::before,
  .auth-divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  /* PAT section */
  .pat-toggle {
    background: none;
    border: none;
    color: var(--accent);
    cursor: pointer;
    font-size: 0.85rem;
    padding: 0;
    text-decoration: underline;
  }
  .pat-toggle:hover {
    color: var(--text);
  }

  /* OAuth pending */
  .oauth-pending {
    text-align: center;
    padding: 2rem 1rem;
  }
  .oauth-pending h3 {
    margin-top: 1rem;
  }
  .spinner {
    width: 40px;
    height: 40px;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    margin: 0 auto;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  /* Auth connected state */
  .auth-connected {
    padding: 0.2rem 0;
  }
  .auth-user {
    display: flex;
    align-items: center;
    gap: 0.8rem;
  }
  .auth-avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    border: 2px solid var(--accent-green);
  }
  .auth-avatar-placeholder {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: var(--bg);
    border: 2px solid var(--accent-green);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.2rem;
  }
  .auth-info {
    display: flex;
    flex-direction: column;
    flex: 1;
  }
  .auth-name {
    font-weight: 600;
    font-size: 0.95rem;
  }
  .auth-status-text {
    font-size: 0.8rem;
    color: var(--accent-green);
  }

  /* Auth steps */
  .auth-steps {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    margin-top: 0.5rem;
  }
  .auth-step {
    display: flex;
    align-items: flex-start;
    gap: 0.8rem;
  }
  .step-number {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--accent);
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.85rem;
    font-weight: 600;
    flex-shrink: 0;
    margin-top: 0.15rem;
  }
  .step-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .step-hint {
    font-size: 0.78rem;
    color: var(--text-muted);
  }
  .token-input-row {
    display: flex;
    gap: 0.5rem;
  }
  .token-input-row input {
    flex: 1;
  }
  .token-input-row .btn {
    white-space: nowrap;
  }

  /* Button sizes */
  .btn-sm {
    padding: 0.35rem 0.7rem;
    font-size: 0.82rem;
  }
  .form-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    margin-bottom: 0.8rem;
  }
  label span {
    font-size: 0.85rem;
    color: var(--text-muted);
  }
  input, .select {
    padding: 0.6rem 0.8rem;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--text);
    font-size: 0.9rem;
    outline: none;
    transition: border-color 0.2s;
  }
  input:focus, .select:focus {
    border-color: var(--accent);
  }

  /* Drop zone */
  .drop-zone {
    border: 2px dashed var(--border);
    border-radius: var(--radius);
    padding: 3rem 2rem;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }
  .drop-zone:hover, .drop-zone.drag-over {
    border-color: var(--accent);
    background: rgba(108, 99, 255, 0.05);
  }
  .drop-zone.has-file {
    border-color: var(--accent-green);
    background: rgba(34, 197, 94, 0.05);
  }
  .drop-prompt {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
    color: var(--text-muted);
  }
  .drop-icon {
    font-size: 2rem;
  }
  .file-info {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
  }
  .file-icon {
    font-size: 1.5rem;
  }
  .file-name {
    font-weight: 600;
  }
  .file-size {
    color: var(--text-muted);
    font-size: 0.85rem;
  }

  /* Options */
  .options-section h3 {
    margin-bottom: 1rem;
  }
  .option-group {
    margin-bottom: 1rem;
  }
  .option-label {
    font-size: 0.85rem;
    color: var(--text-muted);
    margin-bottom: 0.4rem;
    display: block;
  }
  .toggle-group {
    display: flex;
    gap: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .toggle-btn {
    flex: 1;
    padding: 0.5rem 1rem;
    background: var(--bg);
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.9rem;
    transition: background 0.2s, color 0.2s;
  }
  .toggle-btn:not(:last-child) {
    border-right: 1px solid var(--border);
  }
  .toggle-btn.active {
    background: var(--accent);
    color: white;
  }
  .select {
    width: 100%;
  }

  /* Submit */
  .submit-btn {
    width: 100%;
    padding: 0.8rem;
    font-size: 1rem;
  }
  .submit-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .error-msg {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: #ef4444;
    padding: 0.8rem 1rem;
    border-radius: var(--radius-sm);
    font-size: 0.9rem;
  }

  /* Success */
  .success-panel {
    text-align: center;
    padding: 2rem;
    background: var(--bg-card);
    border: 1px solid var(--accent-green);
    border-radius: var(--radius);
  }
  .success-icon {
    width: 60px;
    height: 60px;
    border-radius: 50%;
    background: var(--accent-green);
    color: white;
    font-size: 2rem;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 auto 1rem;
  }
  .success-panel h2 {
    margin-bottom: 0.5rem;
  }
  .success-panel .muted {
    color: var(--text-muted);
    font-size: 0.9rem;
    margin-top: 0.5rem;
  }
  .success-actions {
    display: flex;
    gap: 1rem;
    justify-content: center;
    margin-top: 1.5rem;
  }
</style>
