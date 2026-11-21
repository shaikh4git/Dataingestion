import hashlib
import os
import uuid

from pathlib import Path
from urllib.parse import urlparse

from docling.document_converter import DocumentConverter

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent

DOCUMENTS_ROOT = Path(
    os.getenv(
        "DOCUMENTS_ROOT",
        str(PROJECT_ROOT / "documents" / "Websites" / "madinps"),
    )
)

QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://localhost:6333",
)

QDRANT_COLLECTION = os.getenv(
    "QDRANT_COLLECTION",
    "madinps_docs",
)

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)

VECTOR_SIZE = 384

INCLUDE_DEPARTMENT_DOCUMENTS = (
    os.getenv("INCLUDE_DEPARTMENT_DOCUMENTS", "false").lower() == "true"
)

WEBSITE_DOMAIN = "madinps.edu.in"

SUPPORTED_EXTENSIONS = {
    ".md",
    ".txt",
    ".pdf",
    ".docx",
    ".pptx",
}

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120


# ============================================================
# DOCUMENT EXTRACTION
# ============================================================

def load_file(path, converter=None):
    """Extract text from supported document formats."""

    extension = path.suffix.lower()

    if extension in {".md", ".txt"}:
        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    if converter is None:
        converter = DocumentConverter()

    result = converter.convert(str(path))

    return result.document.export_to_markdown()


def extract_source_url(content):
    """Extract source_url metadata from Markdown content."""

    for line in content.splitlines()[:10]:
        line = line.strip()

        if line.startswith("source_url:"):
            return line.split(":", 1)[1].strip()

    return None


# ============================================================
# RBAC DOCUMENT CLASSIFICATION
# ============================================================

def classify_document(relative_path, source_url=None):
    """Apply the existing public and department access rules."""

    if source_url:
        if not source_url.startswith("https://"):
            raise ValueError("Only HTTPS website URLs are permitted")

        parsed = urlparse(source_url)

        if parsed.hostname != WEBSITE_DOMAIN:
            raise ValueError(
                f"Unapproved website domain: {parsed.hostname}"
            )

        return {
            "source": relative_path.as_posix(),
            "url": source_url,
            "domain": WEBSITE_DOMAIN,
            "department": "Public",
            "access_scope": "authenticated",
            "allowed_roles": [],
            "content_type": "website",
        }

    department = (
        relative_path.parts[0]
        if len(relative_path.parts) > 1
        else "General"
    )

    if department == "General":
        allowed_roles = ["Employee", "C-Level"]
    else:
        allowed_roles = [department, "C-Level"]

    return {
        "source": relative_path.as_posix(),
        "department": department,
        "access_scope": "restricted",
        "allowed_roles": allowed_roles,
        "content_type": "department_document",
    }


# ============================================================
# DOCUMENT LOADING
# ============================================================

def load_documents():
    """Load documents and attach their RBAC metadata."""

    if not DOCUMENTS_ROOT.exists():
        raise FileNotFoundError(
            f"Documents directory does not exist: {DOCUMENTS_ROOT}"
        )

    documents = []
    converter = DocumentConverter()

    for path in sorted(DOCUMENTS_ROOT.rglob("*")):

        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        relative_path = path.relative_to(DOCUMENTS_ROOT)

        try:
            content = load_file(path, converter)

            if not content.strip():
                continue

            source_url = extract_source_url(content)

            # Preserve the existing department-document exclusion.
            if (
                not source_url
                and not INCLUDE_DEPARTMENT_DOCUMENTS
            ):
                continue

            metadata = classify_document(
                relative_path,
                source_url,
            )

            documents.append(
                Document(
                    page_content=content,
                    metadata=metadata,
                )
            )

        except Exception as exc:
            print(f"Skipping {path}: {exc}")

    return documents


# ============================================================
# STABLE CHUNK IDENTIFIERS
# ============================================================

def stable_chunk_id(document, index):
    """Generate deterministic IDs for document chunks."""

    source = document.metadata.get("source", "")

    content_hash = hashlib.sha256(
        document.page_content.encode("utf-8")
    ).hexdigest()

    identity = f"{source}:{index}:{content_hash}"

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            identity,
        )
    )


# ============================================================
# QDRANT COLLECTION MANAGEMENT
# ============================================================

def ensure_collection(client):
    """
    Create the collection if missing.
    Validate and reuse it if already present.
    Never delete or recreate an existing collection.
    """

    if not client.collection_exists(QDRANT_COLLECTION):

        print(f"Creating collection: {QDRANT_COLLECTION}")

        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )

        print("Collection created successfully.")

        return

    print(f"Collection already exists: {QDRANT_COLLECTION}")

    collection = client.get_collection(QDRANT_COLLECTION)

    vectors = collection.config.params.vectors

    # This ingestion workflow expects one unnamed dense vector.
    if not isinstance(vectors, VectorParams):
        raise RuntimeError(
            "Incompatible collection configuration. "
            "Expected one unnamed dense vector. "
            "No collection changes were made."
        )

    if vectors.size != VECTOR_SIZE:
        raise RuntimeError(
            f"Vector dimension mismatch: expected {VECTOR_SIZE}, "
            f"found {vectors.size}. No collection changes were made."
        )

    if vectors.distance != Distance.COSINE:
        raise RuntimeError(
            f"Distance mismatch: expected COSINE, "
            f"found {vectors.distance}. "
            "No collection changes were made."
        )

    print("Existing collection configuration validated.")


# ============================================================
# INGESTION
# ============================================================

def ingest():

    print("========== INGESTION CONFIGURATION ==========")
    print(f"Documents directory : {DOCUMENTS_ROOT}")
    print(f"Qdrant URL           : {QDRANT_URL}")
    print(f"Collection           : {QDRANT_COLLECTION}")
    print(f"Embedding model      : {EMBEDDING_MODEL}")
    print(f"Vector dimensions    : {VECTOR_SIZE}")
    print(f"Include departments  : {INCLUDE_DEPARTMENT_DOCUMENTS}")

    client = QdrantClient(
        url=QDRANT_URL,
        timeout=30,
    )

    try:
        ensure_collection(client)

        documents = load_documents()

        if not documents:
            print("No documents found for ingestion.")
            return

        print(f"Documents loaded: {len(documents)}")

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )

        chunks = splitter.split_documents(documents)

        if not chunks:
            print("No chunks generated.")
            return

        ids = [
            stable_chunk_id(chunk, index)
            for index, chunk in enumerate(chunks)
        ]

        print(f"Chunks generated: {len(chunks)}")
        print("Loading embedding model...")

        embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
        )

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=QDRANT_COLLECTION,
            embedding=embeddings,
        )

        print("Upserting document chunks into Qdrant...")

        vector_store.add_documents(
            documents=chunks,
            ids=ids,
        )

        collection_info = client.get_collection(
            QDRANT_COLLECTION
        )

        print()
        print("========== INGESTION COMPLETED ==========")
        print(f"Documents loaded : {len(documents)}")
        print(f"Chunks processed : {len(chunks)}")
        print(f"Collection       : {QDRANT_COLLECTION}")
        print(f"Qdrant points    : {collection_info.points_count}")
        print(f"Collection status: {collection_info.status}")

    finally:
        client.close()


if __name__ == "__main__":
    ingest()
