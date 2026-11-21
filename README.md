# Ma'din Public School — Website Crawling and Data Ingestion

This project collects publicly accessible pages from the Ma'din Public School website, stores the extracted text as Markdown, prepares document chunks, generates embeddings, and indexes those chunks in Qdrant for retrieval by the chatbot.

The crawler and ingestion pipeline are separate steps:

1. **Crawl** the public website and save Markdown documents.
2. **Review** the extracted Markdown files.
3. **Ingest** the approved documents into Qdrant.

> The crawler is an HTML-page crawler. It does not download or extract PDF, Word, Excel, PowerPoint, image, or other linked attachments.

## 1. Project structure

```text
Data-Ingestion/
├── crawler/
│   ├── __init__.py
│   └── website_crawler.py
├── documents/
│   └── Websites/
│       └── madinps/             # Generated Markdown website pages
├── documents_archive/          # Archived/internal source documents; not crawled
├── ingest_docs.py               # Chunking, embedding and Qdrant ingestion
├── requirements.txt
├── requirements-dev.txt
├── requirements-rbac.txt
└── README.md
```

The crawler writes to `documents/Websites/madinps/`, relative to this project directory.

## 2. Technology

| Component | Technology | Purpose |
|---|---|---|
| HTTP client | `requests` | Fetch website pages, robots.txt and sitemaps |
| HTML parsing | Beautiful Soup 4 | Extract page text and internal links |
| URL/XML handling | Python standard library | Normalize URLs and parse XML sitemaps |
| Document output | Markdown | Store extracted page content and crawl metadata |
| Document conversion | Docling | Extract text from supported files during ingestion |
| Text splitting | LangChain text splitters | Create overlapping text chunks |
| Embeddings | Sentence Transformers | Convert chunks to vectors |
| Vector database | Qdrant | Store vectors and metadata |

## 3. Environment setup

Use Python 3.11 or another Python version supported by the installed dependencies.

From the `Data-Ingestion` directory:

```bash
cd "$HOME/Learning/AI-ML-Projects/Data-Ingestion"

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

The crawler specifically requires:

```bash
pip install requests beautifulsoup4
```

The project `requirements.txt` already lists both packages.

For development/test dependencies:

```bash
pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes the main requirements and adds `pytest` and `ragas`.

## 4. Website crawler

### Website scope

The crawler is configured for:

- Base URL: `https://madinps.edu.in/`
- Allowed host: `madinps.edu.in`
- User-Agent: `MadinPSKnowledgeBot/1.0 (public website indexing)`
- Default maximum pages: `500`
- Request timeout: `20` seconds
- Delay between page requests: `1.5` seconds

It discovers candidate pages from sitemap URLs declared in `robots.txt`. If no sitemap is declared, it tries `/sitemap.xml`. It also discovers internal links from each crawled HTML page.

The crawler:

- Normalizes URLs to HTTPS on the configured domain.
- Removes URL fragments and query parameters during canonical URL discovery.
- Checks `robots.txt` before fetching each page.
- Skips configured private/student workflow paths such as `/login`, `/wp-admin`, `/wp-login`, `/student-portal`, `/results` and `/result-portal`.
- Skips common non-HTML asset extensions, including PDF and office files.
- Extracts text from `main`, `article`, `.entry-content`, or the page body, in that preference order.
- Removes common non-content elements such as scripts, navigation, footer, header, forms and sidebars.
- Saves each non-empty page as a Markdown file.

The crawler is intended for publicly accessible pages only. Respect the website's robots rules and terms of use. Do not use it to access authenticated areas or personal/student records.

### Run the crawler

From the project root:

```bash
python -m crawler.website_crawler --max-pages 500
```

For a smaller test crawl:

```bash
python -m crawler.website_crawler --max-pages 10
```

The crawler prints each page being crawled, the output filename, failures, pages visited, and the output directory.

### Output format

Each page is saved as a Markdown file with a stable filename derived from its URL. The file begins with metadata similar to:

```markdown
<!--
source_url: https://madinps.edu.in/example-page/
domain: madinps.edu.in
crawled_at: 2026-11-12T02:10:13+00:00
-->

# Page title

Source: https://madinps.edu.in/example-page/

Crawled at: 2026-11-12T02:10:13+00:00

---

Extracted page text...
```

The `source_url` metadata is read by `ingest_docs.py` and used to validate the source domain and classify the page as website content.

### Review crawler output

```bash
find documents/Websites/madinps -type f -name '*.md' | wc -l

grep -rl '^source_url:' documents/Websites/madinps --include='*.md' | wc -l

find documents/Websites/madinps -type f -name '*.md' | head
```

To inspect one document:

```bash
file="$(find documents/Websites/madinps -type f -name '*.md' | head -1)"
sed -n '1,35p' "$file"
```

The crawler overwrites the Markdown file for a URL when it crawls that URL again. It does not automatically remove old Markdown files for pages that have disappeared from the website. Review and clean stale source files deliberately if required.

## 5. Document ingestion into Qdrant

`ingest_docs.py` loads the Markdown files, classifies them, splits them into chunks, generates embeddings and upserts the chunks into Qdrant.

### Default configuration

| Setting | Default | Purpose |
|---|---|---|
| `DOCUMENTS_ROOT` | `documents/Websites/madinps` | Source documents to ingest |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint for local execution |
| `QDRANT_COLLECTION` | `madinps_docs` | Target collection |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| Vector dimensions | `384` | Vector size for the configured model |
| Distance | `Cosine` | Qdrant vector distance |
| `CHUNK_SIZE` | `900` | Maximum chunk size |
| `CHUNK_OVERLAP` | `120` | Overlap between chunks |
| `INCLUDE_DEPARTMENT_DOCUMENTS` | `false` | Exclude documents without website source metadata by default |

Environment variables can override the defaults. For example:

```bash
export QDRANT_URL="http://localhost:6333"
export QDRANT_COLLECTION="madinps_docs"
```

When running locally against the Kubernetes Qdrant Service, keep the port-forward running in a separate terminal:

```bash
kubectl port-forward -n qdrant-space service/qdrant 6333:6333
```

The in-cluster service address is:

```text
http://qdrant.qdrant-space.svc.cluster.local:6333
```

That in-cluster DNS name is for workloads inside Kubernetes; a Mac-hosted Python process should use the local port-forward endpoint.

### RBAC metadata

Website pages with a valid `source_url` are classified with metadata:

```json
{
  "domain": "madinps.edu.in",
  "department": "Public",
  "access_scope": "authenticated",
  "allowed_roles": [],
  "content_type": "website"
}
```

The ingestion code validates that the URL uses HTTPS and that its hostname is exactly `madinps.edu.in`.

Department-document classification is also present in the ingestion script. Department documents are excluded by default because `INCLUDE_DEPARTMENT_DOCUMENTS=false`. Do not enable that setting unless the source documents and role mappings have been reviewed and the intended access policy is confirmed.

### Dry-run document and chunk check

Before writing to Qdrant, inspect the number of documents, metadata classifications and chunks:

```bash
python - <<'PY'
from collections import Counter
from ingest_docs import load_documents, CHUNK_SIZE, CHUNK_OVERLAP
from langchain_text_splitters import RecursiveCharacterTextSplitter

documents = load_documents()
print("Documents loaded:", len(documents))
print("Departments:", Counter(d.metadata["department"] for d in documents))
print("Access scopes:", Counter(d.metadata["access_scope"] for d in documents))
print("Content types:", Counter(d.metadata["content_type"] for d in documents))

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)
chunks = splitter.split_documents(documents)
print("Chunks generated:", len(chunks))
print("First chunk metadata:", chunks[0].metadata if chunks else "No chunks")
print("Dry run only; Qdrant was not modified.")
PY
```

### Run ingestion

Ensure Qdrant is reachable, then run:

```bash
python ingest_docs.py
```

The script creates `madinps_docs` if it does not exist. The collection is configured for 384-dimensional Cosine vectors. If the collection already exists, the script validates the vector configuration and reuses it. It fails safely if the configuration is incompatible.

The script generates deterministic chunk IDs and upserts the chunks. It does not delete or recreate the collection.

> **Important:** Deterministic IDs make repeated ingestion of unchanged chunks idempotent. If source content changes and produces different chunk IDs, older chunks may remain in the collection. Plan a deliberate stale-point reconciliation process before relying on repeated crawls as a full synchronization mechanism. Do not delete the collection as a shortcut.

## 6. Verify Qdrant

Check Qdrant health:

```bash
curl -sS http://localhost:6333/healthz
```

List collections:

```bash
curl -sS http://localhost:6333/collections | python -m json.tool
```

Inspect the collection configuration:

```bash
curl -sS http://localhost:6333/collections/madinps_docs | python -m json.tool
```

The expected vector configuration is size `384` and distance `Cosine`. Check the reported point count after ingestion.

## 7. Recommended operating sequence

For a normal refresh:

1. Activate the Python virtual environment.
2. Start or verify the Qdrant port-forward.
3. Run the crawler with an appropriate page limit.
4. Review the generated Markdown documents and source URLs.
5. Run the dry-run document/chunk check.
6. Run `python ingest_docs.py`.
7. Verify the Qdrant collection and point count.

Crawling and ingestion are manual data-maintenance operations. They should not be run automatically as part of the chatbot's ordinary application startup or every application CI/CD build.

## 8. Troubleshooting

| Symptom | Checks |
|---|---|
| `ModuleNotFoundError: requests` or `bs4` | Activate `.venv` and run `pip install -r requirements.txt`. |
| Crawler reports robots restrictions | Check the site's current `robots.txt`; do not bypass disallowed paths. |
| Sitemap cannot be parsed | Check sitemap availability and XML validity. |
| No Markdown files are generated | Check network access, the page limit, robots rules, and crawler output directory. |
| Ingestion cannot connect to Qdrant | Check Qdrant health and that the port-forward is still running. |
| Collection vector mismatch | Verify the existing collection's vector size and distance; do not delete/recreate it automatically. |
| Embedding model download is slow | The model may need to download on first use; check network access and available disk space. |
| Documents are skipped | Review the printed exception and confirm supported file extension and valid website metadata. |

## 9. Security and data handling

- Crawl only public pages and respect robots rules and website policies.
- Do not crawl login, student portal, result portal, or other private areas.
- Review extracted content before indexing it.
- Keep confidential department source files out of public Git history.
- Keep API keys, credentials and Qdrant administrative access outside source control.
- Preserve document-level RBAC metadata and test retrieval authorization in the chatbot.
- Restrict network access to Qdrant; use appropriate authentication, TLS and network policies for production deployments.

## 10. Current scope and limitations

- The crawler extracts HTML page text; it does not extract linked PDF or office attachments.
- The crawler uses a configured domain and path exclusion list; review these if the website structure changes.
- The crawler does not automatically delete Markdown files for pages no longer present on the site.
- The ingestion script supports Markdown, text, PDF, DOCX and PPTX files, subject to Docling support and installed dependencies.
- The current website source set is stored under `documents/Websites/madinps`.
