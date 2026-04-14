import os
from pathlib import Path

# Base paths
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "persistence" / "uploaded_data"
_memory_root = os.environ.get("MEMORY_ROOT")
MEMORY_DIR = Path(_memory_root) if _memory_root else REPO_ROOT / "persistence" / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# SOP specific paths
_preferred_sop_dir = DATA_DIR / "SOPs"
_legacy_sop_dir = DATA_DIR / "SOP"
if _preferred_sop_dir.exists():
    SOP_DATA_DIR = _preferred_sop_dir
elif _legacy_sop_dir.exists():
    SOP_DATA_DIR = _legacy_sop_dir
else:
    SOP_DATA_DIR = _preferred_sop_dir

SOP_MEMORY_DIR = MEMORY_DIR / "sop_documents"
CHROMA_PERSIST_PATH = SOP_MEMORY_DIR / "chroma_db" / "sop_rag"
DOCSTORE_PATH = SOP_MEMORY_DIR / "docstore.pkl"

# ChromaDB configuration
COLLECTION_NAME = 'sop_rag'
ID_KEY = 'doc_id'

# PDF processing configuration
PDF_PROCESSING_CONFIG = {
    'strategy': 'hi_res',
    'infer_table_structure': True,
    'extract_image_block_types': ['Image'],
    'extract_image_block_to_payload': True,
    'chunking_strategy': 'by_title',
    'max_characters': 10000,
    'combine_text_under_n_chars': 2000,
    'new_after_n_chars': 6000,
}

# LLM configuration
LLM_CONFIG = {
    'summarization_model': 'gpt-5.1',
    'image_description_model': 'gpt-5.1',
    'rag_response_model': 'gpt-5.1'
}

# Retrieval configuration
RETRIEVAL_CONFIG = {
    'search_type': 'similarity_score_threshold',
    'default_score_threshold': 0.3,
    'max_results': 12,
}

def ensure_directories():
    """Ensure all necessary directories exist."""
    directories = [
        SOP_DATA_DIR,
        SOP_MEMORY_DIR,
        CHROMA_PERSIST_PATH,
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    # Test configuration
    print("SOP RAG Configuration:")
    print(f"Repository root: {REPO_ROOT}")
    print(f"SOP data directory: {SOP_DATA_DIR}")
    print(f"ChromaDB path: {CHROMA_PERSIST_PATH}")
    print(f"Collection name: {COLLECTION_NAME}")
    
    # Ensure directories exist
    ensure_directories()
    print("\nDirectories created successfully!")
