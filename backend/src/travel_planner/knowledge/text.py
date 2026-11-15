import re
import threading

import jieba
import jieba.posseg

_tokenizer = jieba.Tokenizer()
_pos_tokenizer = jieba.posseg.POSTokenizer(_tokenizer)
_token_lock = threading.Lock()
jieba.setLogLevel(30)


def tokens(text: str) -> list[str]:
    with _token_lock:
        words = _tokenizer.cut_for_search(text.casefold())
        return list(dict.fromkeys(word for word in words if re.search(r"\w", word)))


def relevant_to_destination(text: str, destination: str) -> bool:
    city = destination.removesuffix("市")
    if city in text:
        return True
    with _token_lock:
        # 未提及本城而包含其他地名的片段留在本地；通用旅行资料仍可命中。
        return not any(word.flag == "ns" for word in _pos_tokenizer.cut(text))


def split_chunks(segments: list[dict], size: int = 800, overlap: int = 100) -> list[dict]:
    chunks = []
    for segment in segments:
        text = segment["text"].strip()
        for offset in range(0, len(text), size - overlap):
            chunks.append(
                {
                    "text": text[offset : offset + size],
                    "offset": offset,
                    "page": segment.get("page"),
                    "paragraph": segment.get("paragraph"),
                }
            )
            if offset + size >= len(text):
                break
    return chunks
