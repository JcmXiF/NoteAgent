"""The local writing quality cases: a request, its sources, and judge rules.

A case starts with one or more selected pages already in the writing state.
The model draws frames, composes sections and publishes; the judge reads the
result against the rules below. The rules are the expectations, not a copy of
what the implementation happens to do.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SelectedSource:
    """One selected page: its blocks, as the state would hold them."""

    url: str
    title: str
    blocks: tuple[str, ...]

    def html(self) -> str:
        paragraphs = "".join(
            f'<p data-na-block="b{index}">{text}</p>'
            for index, text in enumerate(self.blocks, 1)
        )
        return f"<html><body><article>{paragraphs}</article></body></html>"

    def text(self) -> str:
        return "\n".join(
            f"[b{index}] {text}"
            for index, text in enumerate(self.blocks, 1)
        )


@dataclass(frozen=True, slots=True)
class WritingCase:
    """One quality case."""

    name: str
    title: str
    request: str
    sources: tuple[SelectedSource, ...]
    #: dimension -> {"must": [...], "must_not": [...]}
    rules: Mapping[str, Mapping[str, tuple[str, ...]]]


def _rules(
    coverage: tuple[tuple[str, ...], tuple[str, ...]],
    citations: tuple[tuple[str, ...], tuple[str, ...]],
    structure: tuple[tuple[str, ...], tuple[str, ...]],
    coherence: tuple[tuple[str, ...], tuple[str, ...]],
) -> dict[str, Mapping[str, tuple[str, ...]]]:
    return {
        "coverage": {"must": coverage[0], "must_not": coverage[1]},
        "citations": {"must": citations[0], "must_not": citations[1]},
        "structure": {"must": structure[0], "must_not": structure[1]},
        "coherence": {"must": coherence[0], "must_not": coherence[1]},
    }


ENTROPY = WritingCase(
    name="entropy",
    title="信息熵",
    request="把选中的资料整理成一页关于信息熵的 wiki 页。",
    sources=(
        SelectedSource(
            url="https://example.com/entropy",
            title="Entropy",
            blocks=(
                "信息熵衡量一个随机变量的平均不确定性；概率越均匀，熵越大。",
                "信息熵的定义是 H(X) = -Σ p(x) log p(x)。",
                "交叉熵衡量用分布 q 编码真实分布 p 的平均代价：H(p,q) = H(p) + KL(p||q)。",
                "熵越大，说明不确定性越高，平均信息量越大。",
            ),
        ),
    ),
    rules=_rules(
        (
            ("给出信息熵的定义与公式", "说明熵与不确定性的关系", "给出交叉熵与熵的关系"),
            ("遗漏交叉熵",),
        ),
        (
            ("公式与事实来自资料块",),
            ("编造资料里没有的来源",),
        ),
        (
            ("有标题与清晰的 section",),
            ("只有链接或要点堆砌",),
        ),
        (
            ("读起来是一页连贯的 wiki",),
            ("自相矛盾",),
        ),
    ),
)

BAYES = WritingCase(
    name="bayes",
    title="贝叶斯定理",
    request="把选中的资料整理成一页关于贝叶斯定理的 wiki 页。",
    sources=(
        SelectedSource(
            url="https://example.com/bayes",
            title="Bayes' theorem",
            blocks=(
                "贝叶斯定理：P(A|B) = P(B|A) P(A) / P(B)。",
                "P(A) 是先验概率，P(B|A) 是似然，P(A|B) 是后验概率。",
                "P(B) 是证据，也叫边际似然；它把后验归一化。",
                "贝叶斯更新用后验概率替代先验概率，再遇到新证据时重复这个过程。",
            ),
        ),
    ),
    rules=_rules(
        (
            ("给出贝叶斯定理的公式", "解释先验、似然、后验", "说明贝叶斯更新"),
            ("把后验与先验混为一谈",),
        ),
        (
            ("公式与术语来自资料块",),
            ("编造资料里没有的来源",),
        ),
        (
            ("有标题与清晰的 section",),
            ("只有链接或要点堆砌",),
        ),
        (
            ("读起来是一页连贯的 wiki",),
            ("自相矛盾",),
        ),
    ),
)


def writing_cases() -> tuple[WritingCase, ...]:
    return (ENTROPY, BAYES)


__all__ = ["BAYES", "ENTROPY", "SelectedSource", "WritingCase", "writing_cases"]
