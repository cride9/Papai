"""
downsample_chromadb_1024.py — 2048 dimenziós ChromaDB collection levágása 1024-re.

Nem hívja újra az embedding endpointot. Mivel a Qwen3 embedding Matryoshka-
reprezentáció, a levágás + renormalizálás kommutál a normalizálással:

    normalize(x)[:1024] / ||normalize(x)[:1024]||  ==  normalize(x[:1024])

vagyis a már 2048-ra vágott + normalizált vektorok első 1024 elemének
újra-renormalizálása PONTOSAN ugyanazt az eredményt adja, mintha a natív
4096-os vektort vágtad volna le közvetlenül 1024-re. Ezért ez a script
biztonságosan használható a create_chromadb.py (2048 dim) kimenetéből
kiindulva, embedding API hívás nélkül.

Indítás:
    python downsample_chromadb_1024.py --reset
"""

import os
import argparse
import numpy as np
import chromadb
from tqdm import tqdm

# ==========================================
# KONFIGURÁCIÓ
# ==========================================
SOURCE_CHROMA_DB_PATH = "./databases/chroma_text_db_deploy"
SOURCE_COLLECTION_NAME = "alkatreszek_hu_2048"

TARGET_CHROMA_DB_PATH = "./databases/chroma_text_db_deploy_1024"
TARGET_COLLECTION_NAME = "alkatreszek_hu_1024"

TARGET_DIM = 1024

READ_BATCH_SIZE = 500
WRITE_BATCH_SIZE = 500


def truncate_and_normalize(vec, target_dim):
    arr = np.array(vec[:target_dim], dtype=np.float32)
    norm = np.linalg.norm(arr)
    return (arr / norm).tolist() if norm > 0 else arr.tolist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                         help="Törli a meglévő (1024-es) collectiont és mindent elölről kezd.")
    args = parser.parse_args()

    print("=" * 60)
    print(f"🚀 ChromaDB downsample: 2048 dim -> {TARGET_DIM} dim (nincs újra-embeddelés)")
    print("=" * 60)

    src_client = chromadb.PersistentClient(path=SOURCE_CHROMA_DB_PATH)
    src_collection = src_client.get_collection(name=SOURCE_COLLECTION_NAME)

    dst_client = chromadb.PersistentClient(path=TARGET_CHROMA_DB_PATH)
    if args.reset:
        print("⚠️  --reset kapcsoló aktív: meglévő cél collection törlése...")
        try:
            dst_client.delete_collection(name=TARGET_COLLECTION_NAME)
        except Exception:
            pass
    dst_collection = dst_client.get_or_create_collection(name=TARGET_COLLECTION_NAME)

    print("🔍 Már meglévő (1024-es) sorok ellenőrzése (resume)...")
    existing_ids = set(dst_collection.get(include=[])["ids"])
    print(f"✅ {len(existing_ids)} sor már benne van, ezeket kihagyjuk.")

    total_src = src_collection.count()
    print(f"📄 Forrás collection ({SOURCE_COLLECTION_NAME}): {total_src} sor.")

    processed = 0
    written = 0
    offset = 0

    pbar = tqdm(total=total_src, desc="Downsample")
    while offset < total_src:
        batch = src_collection.get(
            include=["embeddings", "documents", "metadatas"],
            limit=READ_BATCH_SIZE,
            offset=offset,
        )

        ids = batch["ids"]
        if not ids:
            break

        keep_idx = [i for i, _id in enumerate(ids) if _id not in existing_ids]
        if keep_idx:
            new_ids = [ids[i] for i in keep_idx]
            new_embeddings = [
                truncate_and_normalize(batch["embeddings"][i], TARGET_DIM)
                for i in keep_idx
            ]
            new_documents = [batch["documents"][i] for i in keep_idx]
            new_metadatas = [batch["metadatas"][i] for i in keep_idx]

            for i in range(0, len(new_ids), WRITE_BATCH_SIZE):
                dst_collection.upsert(
                    ids=new_ids[i:i + WRITE_BATCH_SIZE],
                    embeddings=new_embeddings[i:i + WRITE_BATCH_SIZE],
                    documents=new_documents[i:i + WRITE_BATCH_SIZE],
                    metadatas=new_metadatas[i:i + WRITE_BATCH_SIZE],
                )
            written += len(new_ids)

        processed += len(ids)
        offset += READ_BATCH_SIZE
        pbar.update(len(ids))

    pbar.close()

    print("\n" + "=" * 60)
    print("🎉 KÉSZ!")
    print(f"📊 Feldolgozott sor: {processed} | Újonnan írt sor: {written} | "
          f"Már kész volt: {processed - written}")
    print(f"📁 Cél ChromaDB: {TARGET_CHROMA_DB_PATH} | Collection: {TARGET_COLLECTION_NAME}")
    print("⚠️  FONTOS: a query-oldalon (embedding_service.py) is target_dim=1024-re")
    print("    kell állítani a truncate_and_normalize hívást, hogy a query-vektor")
    print("    és a tárolt vektorok ugyanabban a térben legyenek.")
    print("=" * 60)


if __name__ == "__main__":
    main()