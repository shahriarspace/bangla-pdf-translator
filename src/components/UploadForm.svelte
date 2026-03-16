<script lang="ts">
  /**
   * UploadForm.svelte
   *
   * Interactive Svelte island for the upload page.
   * Flow:
   * 1. User selects PDF + translation options
   * 2. PDF is uploaded as a GitHub Release asset (via GitHub API)
   * 3. A workflow_dispatch event triggers the translate.yml Action
   * 4. User sees a "submitted" confirmation with link to check Actions status
   *
   * Requires a GitHub PAT with `repo` and `actions` scope, stored in
   * the site config or entered by the user.
   */

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

  // GitHub config — user provides these
  let githubToken = $state('');
  let repoOwner = $state('');
  let repoName = $state('bangla-pdf-to-eng-book');

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
      // Step 1: Upload PDF as a GitHub Release asset
      // Create a release to hold the uploaded file
      const releaseRes = await fetch(
        `https://api.github.com/repos/${repoOwner}/${repoName}/releases`,
        {
          method: 'POST',
          headers: {
            Authorization: `token ${githubToken}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            tag_name: `upload-${slug}-${Date.now()}`,
            name: `Upload: ${file.name}`,
            body: `Auto-created for PDF upload: ${file.name}`,
            draft: true,
          }),
        }
      );

      if (!releaseRes.ok) {
        throw new Error(`Failed to create release: ${releaseRes.status} ${await releaseRes.text()}`);
      }

      const release = await releaseRes.json();
      const uploadUrl = release.upload_url.replace('{?name,label}', `?name=${encodeURIComponent(file.name)}`);

      // Step 2: Upload the PDF to the release
      const uploadRes = await fetch(uploadUrl, {
        method: 'POST',
        headers: {
          Authorization: `token ${githubToken}`,
          'Content-Type': 'application/pdf',
        },
        body: file,
      });

      if (!uploadRes.ok) {
        throw new Error(`Failed to upload PDF: ${uploadRes.status}`);
      }

      const asset = await uploadRes.json();

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
              release_id: String(release.id),
              asset_download_url: asset.browser_download_url,
              translation_mode: translationMode,
              ocr_engine: ocrEngine,
              refinement_provider: refinementProvider,
              refinement_model: refinementProvider !== 'none' ? refinementModel : '',
              ai_provider: needsAiSettings ? aiProvider : 'github',
              ai_ocr_model: ocrEngine === 'ai' ? aiOcrModel : 'gpt-4o',
              ai_translate_model: translationMode === 'ai' ? aiTranslateModel : 'gpt-4o',
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
    <!-- GitHub Config -->
    <div class="config-section card">
      <h3>GitHub Configuration</h3>
      <p class="help-text">A GitHub Personal Access Token (PAT) with <code>repo</code> scope is required to upload files and trigger workflows.</p>
      <div class="form-row">
        <label>
          <span>GitHub Username / Org</span>
          <input type="text" bind:value={repoOwner} placeholder="your-username" />
        </label>
        <label>
          <span>Repository Name</span>
          <input type="text" bind:value={repoName} placeholder="bangla-pdf-to-eng-book" />
        </label>
      </div>
      <label>
        <span>Personal Access Token</span>
        <input type="password" bind:value={githubToken} placeholder="ghp_xxxxxxxxxxxx" />
      </label>
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
      <h3>OCR Engine</h3>

      <div class="option-group">
        <label class="option-label">Text Extraction Method</label>
        <div class="toggle-group">
          <button
            class="toggle-btn"
            class:active={ocrEngine === 'tesseract'}
            onclick={() => ocrEngine = 'tesseract'}
          >
            Tesseract (offline)
          </button>
          <button
            class="toggle-btn"
            class:active={ocrEngine === 'ai'}
            onclick={() => ocrEngine = 'ai'}
          >
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
          <button
            class="toggle-btn"
            class:active={translationMode === 'offline'}
            onclick={() => translationMode = 'offline'}
          >
            Offline (Argos)
          </button>
          <button
            class="toggle-btn"
            class:active={translationMode === 'online'}
            onclick={() => translationMode = 'online'}
          >
            Online (Google)
          </button>
          <button
            class="toggle-btn"
            class:active={translationMode === 'ai'}
            onclick={() => translationMode = 'ai'}
          >
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
      disabled={!file || !githubToken || !repoOwner || status === 'uploading'}
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
