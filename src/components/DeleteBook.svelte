<script lang="ts">
  /**
   * DeleteBook.svelte
   *
   * Interactive delete button for the book detail page.
   *
   * Strategy:
   * 1. Try direct deletion via GitHub Contents API (fast, works for small books)
   * 2. Fall back to triggering delete-book.yml workflow (for large books or API errors)
   *
   * Requires the user to be logged in (reads token from localStorage).
   */
  import { onMount } from 'svelte';

  // Props
  let { slug, bookTitle }: { slug: string; bookTitle: string } = $props();

  const STORAGE_KEY = 'bangla-translator-github';
  const DEFAULT_REPO_OWNER = 'shahriarspace';
  const DEFAULT_REPO_NAME = 'bangla-pdf-translator';

  // State
  let githubToken = $state('');
  let githubUser = $state('');
  let repoOwner = $state(DEFAULT_REPO_OWNER);
  let repoName = $state(DEFAULT_REPO_NAME);
  let isLoggedIn = $state(false);
  let deleteStatus: 'idle' | 'confirm' | 'deleting' | 'done' | 'error' = $state('idle');
  let deleteProgress = $state('');
  let deleteError = $state('');

  onMount(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const data = JSON.parse(saved);
        if (data.token && data.user) {
          githubToken = data.token;
          githubUser = data.user;
          repoOwner = data.repoOwner || DEFAULT_REPO_OWNER;
          repoName = data.repoName || DEFAULT_REPO_NAME;
          isLoggedIn = true;
        }
      }
    } catch {
      // ignore
    }
  });

  function askConfirm() {
    deleteStatus = 'confirm';
  }

  function cancelDelete() {
    deleteStatus = 'idle';
    deleteError = '';
  }

  async function performDelete() {
    deleteStatus = 'deleting';
    deleteError = '';

    try {
      // Step 1: Try direct API deletion
      deleteProgress = 'Listing book files...';
      const deleted = await tryDirectDelete();

      if (deleted) {
        // Step 2: Update catalog.json
        deleteProgress = 'Updating catalog...';
        await updateCatalog();

        deleteStatus = 'done';
        deleteProgress = 'Book deleted successfully. Site will rebuild shortly.';
      }
    } catch (e: any) {
      // Step 3: Fall back to workflow
      console.warn('Direct delete failed, falling back to workflow:', e.message);
      try {
        deleteProgress = 'Triggering delete workflow...';
        await triggerDeleteWorkflow();
        deleteStatus = 'done';
        deleteProgress = 'Delete workflow triggered. The book will be removed shortly.';
      } catch (wfErr: any) {
        deleteStatus = 'error';
        deleteError = wfErr.message || 'Failed to delete book';
      }
    }
  }

  async function tryDirectDelete(): Promise<boolean> {
    // List all files in library/{slug}/
    const apiBase = `https://api.github.com/repos/${repoOwner}/${repoName}/contents`;
    const headers = {
      Authorization: `token ${githubToken}`,
      Accept: 'application/vnd.github.v3+json',
    };

    const dirRes = await fetch(`${apiBase}/library/${slug}`, { headers });

    if (!dirRes.ok) {
      if (dirRes.status === 404) {
        // Directory doesn't exist, just update catalog
        return true;
      }
      throw new Error(`Failed to list files: ${dirRes.status}`);
    }

    const files = await dirRes.json();

    if (!Array.isArray(files)) {
      throw new Error('Unexpected API response — falling back to workflow');
    }

    // Delete each file (Contents API requires individual file deletions)
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      deleteProgress = `Deleting file ${i + 1}/${files.length}: ${file.name}`;

      const delRes = await fetch(`${apiBase}/library/${slug}/${file.name}`, {
        method: 'DELETE',
        headers: {
          ...headers,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: `Delete ${file.name} from ${slug}`,
          sha: file.sha,
          branch: 'main',
        }),
      });

      if (!delRes.ok) {
        const text = await delRes.text();
        throw new Error(`Failed to delete ${file.name}: ${delRes.status} ${text}`);
      }
    }

    return true;
  }

  async function updateCatalog() {
    const apiBase = `https://api.github.com/repos/${repoOwner}/${repoName}/contents`;
    const headers = {
      Authorization: `token ${githubToken}`,
      Accept: 'application/vnd.github.v3+json',
    };

    // Get current catalog.json
    const catRes = await fetch(`${apiBase}/library/catalog.json`, { headers });
    if (!catRes.ok) return; // No catalog to update

    const catData = await catRes.json();
    const currentContent = atob(catData.content.replace(/\n/g, ''));
    const catalog = JSON.parse(currentContent);

    // Remove the book entry
    catalog.books = catalog.books.filter((b: any) => b.slug !== slug);

    // Write updated catalog
    const newContent = btoa(
      unescape(encodeURIComponent(JSON.stringify(catalog, null, 2) + '\n'))
    );

    const updateRes = await fetch(`${apiBase}/library/catalog.json`, {
      method: 'PUT',
      headers: {
        ...headers,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: `Remove ${slug} from catalog`,
        content: newContent,
        sha: catData.sha,
        branch: 'main',
      }),
    });

    if (!updateRes.ok) {
      console.warn('Failed to update catalog via API, workflow will handle it');
    }
  }

  async function triggerDeleteWorkflow() {
    const res = await fetch(
      `https://api.github.com/repos/${repoOwner}/${repoName}/actions/workflows/delete-book.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          Authorization: `token ${githubToken}`,
          'Content-Type': 'application/json',
          Accept: 'application/vnd.github.v3+json',
        },
        body: JSON.stringify({
          ref: 'main',
          inputs: { slug },
        }),
      }
    );

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to trigger delete workflow: ${res.status} ${text}`);
    }
  }
</script>

{#if isLoggedIn}
  <div class="delete-section">
    {#if deleteStatus === 'idle'}
      <button class="btn btn-danger" onclick={askConfirm}>
        Delete Book
      </button>

    {:else if deleteStatus === 'confirm'}
      <div class="confirm-panel">
        <p class="confirm-text">
          Are you sure you want to delete <strong>{bookTitle}</strong>? This cannot be undone.
        </p>
        <div class="confirm-actions">
          <button class="btn btn-danger" onclick={performDelete}>
            Yes, Delete
          </button>
          <button class="btn btn-outline" onclick={cancelDelete}>
            Cancel
          </button>
        </div>
      </div>

    {:else if deleteStatus === 'deleting'}
      <div class="delete-progress">
        <div class="spinner"></div>
        <p>{deleteProgress}</p>
      </div>

    {:else if deleteStatus === 'done'}
      <div class="delete-done">
        <p class="success-text">{deleteProgress}</p>
        <a href={`${import.meta.env.BASE_URL}books/`} class="btn btn-primary">
          Back to Library
        </a>
      </div>

    {:else if deleteStatus === 'error'}
      <div class="delete-error">
        <p class="error-text">{deleteError}</p>
        <button class="btn btn-outline" onclick={cancelDelete}>
          Dismiss
        </button>
      </div>
    {/if}
  </div>
{/if}

<style>
  .delete-section {
    margin-top: 2rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--border);
  }

  .btn-danger {
    background: #dc2626;
    color: white;
    border: none;
    padding: 0.5rem 1.2rem;
    border-radius: var(--radius-sm);
    cursor: pointer;
    font-size: 0.9rem;
    font-weight: 500;
    transition: background 0.2s;
  }
  .btn-danger:hover {
    background: #b91c1c;
  }

  .confirm-panel {
    background: rgba(220, 38, 38, 0.08);
    border: 1px solid rgba(220, 38, 38, 0.25);
    border-radius: var(--radius-sm);
    padding: 1.2rem;
  }
  .confirm-text {
    margin-bottom: 1rem;
    font-size: 0.95rem;
  }
  .confirm-actions {
    display: flex;
    gap: 0.8rem;
  }

  .delete-progress {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 1rem 0;
  }
  .spinner {
    width: 24px;
    height: 24px;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .delete-done {
    display: flex;
    align-items: center;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .success-text {
    color: var(--accent-green);
    font-size: 0.95rem;
  }

  .delete-error {
    display: flex;
    align-items: center;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .error-text {
    color: #ef4444;
    font-size: 0.95rem;
  }
</style>
