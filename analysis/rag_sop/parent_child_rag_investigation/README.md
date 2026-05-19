# parent_child_rag_investigation

Sweep child `chunk_size` / `chunk_overlap` and embedding model for the
parent-child SOP RAG **without re-parsing PDFs**.

## How it works

The expensive parts of the original `../parent_child_rag` pipeline
(Unstructured `hi_res` PDF parsing + GPT image captioning) produce *parent*
Documents that live in a `LocalFileStore` docstore at
`../parent_child_rag/sop_documents/docstore/`. Parents are invariant to child
`chunk_size`, `chunk_overlap`, and embedding model.

This folder reuses those parents as-is and only re-embeds children:

1. Load parents directly from the source docstore.
2. Split with a fresh `RecursiveCharacterTextSplitter(chunk_size, chunk_overlap)`.
3. Embed children with the chosen OpenAI embedding model.
4. Write to a per-variant Chroma collection so variants coexist on disk.

Result: only OpenAI embedding cost per variant — no PDF parse, no image
captioning, no docstore mutation.

## Layout

```
parent_child_rag_investigation/
├── config.py          # paths, defaults, per-variant naming helpers
├── sop_indexer.py     # build / sweep child collections
├── sop_retriever.py   # ParentChildRetriever(chunk_size, chunk_overlap, embedding_model)
└── sop_documents/
    └── chroma_db/
        └── <embedding_model>/
            └── c<chunk_size>_o<chunk_overlap>/   # one Chroma store per variant
```

Variant naming:
- Chroma path: `sop_documents/chroma_db/<embedding_model>/c{chunk_size}_o{chunk_overlap}/`
- Collection name: `sop_rag_<embedding_model>_c{chunk_size}_o{chunk_overlap}`


## Prerequisites

Run the original parent-child indexer **once** so the parent docstore exists:

```bash
python ../parent_child_rag/sop_indexer.py
```

`OPENAI_API_KEY` must be set (read from the repo root `.env`).

## Building variants

```bash
cd parent_child_rag_investigation

# Sweep the default chunk sizes [100, 200, 400, 600, 800, 1000, 1200]
python sop_indexer.py --embedding-model text-embedding-3-large

# Single variant
python sop_indexer.py --chunk-size 800 --chunk-overlap 200

# Force rebuild an existing variant
python sop_indexer.py --chunk-size 400 --overwrite
```

## Querying

```python
from parent_child_rag_investigation.sop_retriever import ParentChildRetriever

# Defaults: embedding_model=text-embedding-3-small, overlap=12.5% of chunk_size
r = ParentChildRetriever(chunk_size=400)
docs = r.query("How to handle pyrophoric reagent spills?")

# Variant winner from this investigation
r_best = ParentChildRetriever(
    chunk_size=400,
    chunk_overlap=50,
    embedding_model="text-embedding-3-large",
)
```