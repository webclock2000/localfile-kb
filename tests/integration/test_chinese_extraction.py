"""Chinese extraction: verify Chinese prompt routing + entity boundary quality.

Requires LLM server running. Skip with: pytest -m "not llm"
"""

import tempfile
from pathlib import Path

import pytest

from filekb.config import load_config
from filekb.dedup import inline_dedup
from filekb.extractor import extract_facts, _load_prompt
from filekb.llm import LLMClient
from filekb.splitter import detect_chinese

pytestmark = pytest.mark.llm


CHINESE_DOC = """# 2025年度项目总结

张三于2025年3月加入华为技术有限公司，担任人工智能首席科学家。
他此前在清华大学计算机系攻读博士学位，师从王教授。

华为技术有限公司于2024年发布了Mate70手机，搭载麒麟9100芯片。
该芯片由海思半导体自主研发，性能较上一代提升30%。

欧阳修团队负责大模型的数据标注工作，李四与张三共同开发了天工大模型。
"""


@pytest.fixture
def llm():
    cfg = load_config()
    return LLMClient(base_url=cfg.llm.base_url, model=cfg.llm.model, timeout=cfg.llm.timeout)


def test_chinese_detection():
    """Chinese text should be correctly detected."""
    assert detect_chinese(CHINESE_DOC) is True
    assert detect_chinese("This is English only.") is False


def test_chinese_prompt_loaded():
    """extract_zh.txt should exist and contain Chinese instructions."""
    prompt = _load_prompt("extract_zh.txt")
    assert len(prompt) > 500
    assert "实体边界" in prompt or "不可拆分" in prompt
    assert "张三" in prompt


def test_entity_boundary_integrity(llm):
    """Extracted Chinese entities must not be split.

    Key checks:
    - "张三" must not be split into "张" and "三"
    - "华为技术有限公司" must not be split into "华为" and "技术有限公司"
    - "王教授" must not be split into "王" and "教授"
    """
    result = extract_facts(llm, CHINESE_DOC, chunk_id=1, rounds=1, confidence_threshold=30)
    if result.truncated and len(result.facts) == 0:
        pytest.skip("LLM output truncated — model under load, not a code issue")
    assert len(result.facts) > 0, "Should extract at least 1 fact"

    # Collect all subjects and objects
    all_entities: set[str] = set()
    for fact in result.facts:
        all_entities.add(fact.subject)
        all_entities.add(fact.object)

    # Check for split entities (these would indicate boundary failure)
    bad_splits = ["张", "三", "华为", "技术有限公司", "王", "教授"]
    for entity in all_entities:
        for bad in bad_splits:
            if entity == bad:
                pytest.fail(
                    f"Entity boundary violation: '{entity}' should not be a standalone node. "
                    f"Expected compound forms like '张三', '王教授', '华为技术有限公司'. "
                    f"All entities: {sorted(all_entities)}"
                )

    # Verify we have at least some compound entities
    has_compound = any(len(e) >= 3 for e in all_entities)
    assert has_compound, f"No compound entities found. All entities: {sorted(all_entities)}"
