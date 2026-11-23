
# Capacity plan: LicenseLens

<!-- generated:start -->
## Measured on my start corpus (2026-11-23 09:20 UTC)

| What | Value |
|---|---|
| Files | 818 |
| Size on disk | 22.3 MB |
| Pages (PDF pages + 1 per 3,000 characters of text/HTML) | 7,376 |
| Characters of text | 22,129,362 |
| Estimated tokens (characters / 4) | 5,532,340 |
| Tokens per page | 750 |
| PDFs with almost no text (scanned? OCR is Day 7) | 0 |
| Files not measured (zip, xlsx, unreadable) | 0 |

## Estimate for the full corpus (target: 2,000 files)

| What | How | Value |
|---|---|---|
| Scale factor | target / measured | x 2.4 |
| Tokens | measured tokens x scale | 13.53 M |
| Raw files | measured MB x scale | 54 MB |
| Chunks | tokens / 400 x 1.15 overlap | 38,889 |
| Embedding cost, one time | tokens x 1.15 x $0.15 per 1M | $2.33 = Rs 222 |
| Vector storage | chunks x 768 numbers x 4 bytes | 119 MB |
| Text in Postgres | characters x scale x 1.2 | 65 MB |

Assumptions: 4 characters per token, 400-token chunks, 768-dimension embeddings at $0.15 per 1M tokens, Rs 95 per US dollar.
<!-- generated:end -->

## What I conclude

1. **Is embedding cost a problem for this product?**

   No. The estimated one-time embedding cost for the 2,000-file target is approximately Rs 222, which is affordable for this project.

2. **Do the vectors and text fit in my free Postgres?**

   Yes. Neon Free provides 1 GB of Postgres storage per project.
   My estimated requirement is 184 MB (119 MB vectors + 65 MB text),
   approximately 18.4% of the available storage.

   This leaves roughly 816 MB for other database data and overhead.
   I will monitor actual database usage after importing and indexing
   the corpus because vector indexes and metadata can increase storage.

3. **Which number am I least sure about, and how will I measure it better?**

   I am least sure about the estimated token count because it uses the assumption of 4 characters per token. I will measure actual tokens using the tokenizer of my chosen embedding model.

4. **What changes if the corpus grows 10 times?**

   The number of documents, tokens, chunks, and storage requirements will increase approximately 10 times if the document mix stays similar.

   Embedding costs will also increase approximately 10 times. I will need to monitor storage, processing time, and cost as the corpus grows.
