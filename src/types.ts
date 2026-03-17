/**
 * Library catalog type definitions.
 * Each book in library/catalog.json follows this shape.
 */
export interface BookEntry {
  slug: string;
  title: string;
  title_bangla?: string;
  author?: string;
  author_bangla?: string;
  category?: string;
  tags?: string[];
  page_count?: number;
  status: 'pending' | 'processing' | 'translated' | 'failed';
  date_added: string;
  date_translated?: string;
  translation_mode?: string;
  refinement_provider?: string;
  refinement_model?: string;
  files?: {
    original_pdf?: string;
    translated_adoc?: string;
    translated_html?: string;
    translated_pdf?: string;
    bilingual_json?: string;
  };
}

export interface Catalog {
  books: BookEntry[];
}
