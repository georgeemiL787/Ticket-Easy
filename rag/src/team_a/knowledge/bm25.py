"""Small Okapi BM25 implementation over pre-tokenized documents."""

import math
from collections import Counter


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_freqs = [Counter(d) for d in docs]
        self.doc_lens = [len(d) for d in docs]
        self.avg_len = (sum(self.doc_lens) / len(docs)) if docs else 0.0
        df = Counter(term for d in docs for term in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        terms = set(query)
        out = []
        for freqs, length in zip(self.doc_freqs, self.doc_lens):
            score = 0.0
            for t in terms:
                f = freqs.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
                score += self.idf[t] * f * (self.k1 + 1) / denom
            out.append(score)
        return out
