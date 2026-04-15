"""样本分组推断健壮模块测试"""

import pytest

from sra_search.inference.group_inference_robust import (
    infer_groups,
    infer_groups_robust,
    pattern_prefix,
    pattern_delimiter,
    pattern_timecourse,
    normalize,
    strip_numeric_suffix,
)


class TestNormalize:
    """工具函数测试"""

    def test_normalize_basic(self):
        assert normalize("  Hello  ") == "hello"
        assert normalize("Con1") == "con1"
        assert normalize("CONTROL_2") == "control_2"


class TestStripNumericSuffix:
    """数字后缀移除测试"""

    def test_strip_suffix(self):
        """只移除末尾数字和分隔符"""
        assert strip_numeric_suffix("Con1") == "Con"
        assert strip_numeric_suffix("GR2") == "GR"
        assert strip_numeric_suffix("GSM123456") == "GSM"
        assert strip_numeric_suffix("day1_week2") == "day1_week"  # 只移除末尾的 2


class TestPatternPrefix:
    """前缀模式识别测试"""

    def test_prefix_basic(self):
        """Case 1: Con1/Con2/Con3 vs GR1/GR2/GR3 vs GS1/GS2/GS3"""
        samples = ["Con1", "Con2", "Con3", "GR1", "GR2", "GR3", "GS1", "GS2", "GS3"]
        result = pattern_prefix(samples)
        assert result is not None
        assert result["method"] == "prefix"
        assert result["confidence"] == 0.95
        assert result["groups"]["con"] == 3
        assert result["groups"]["gr"] == 3
        assert result["groups"]["gs"] == 3

    def test_prefix_binary(self):
        """Case 2: 带下划线的 binary 分组 - 应该走 delimiter 模式"""
        samples = ["Control_1", "Control_2", "Treatment_1", "Treatment_2"]
        result = pattern_prefix(samples)
        # Control_1 有下划线，不匹配 prefix 模式
        assert result is None

    def test_prefix_no_match(self):
        """无前缀模式时返回 None"""
        samples = ["GSM123456", "GSM123457"]
        result = pattern_prefix(samples)
        assert result is None


class TestPatternDelimiter:
    """分隔符模式识别测试"""

    def test_delimiter_basic(self):
        """control_1 / treated_2"""
        samples = ["control_1", "control_2", "treated_1", "treated_2"]
        result = pattern_delimiter(samples)
        assert result is not None
        assert result["method"] == "delimiter"
        assert result["confidence"] == 0.85
        assert result["groups"]["control"] == 2
        assert result["groups"]["treated"] == 2

    def test_delimiter_complex(self):
        """disease_PBMC_1"""
        samples = ["disease_PBMC_1", "disease_PBMC_2", "normal_PBMC_1", "normal_PBMC_2"]
        result = pattern_delimiter(samples)
        assert result is not None
        assert result["method"] == "delimiter"
        assert result["groups"]["disease"] == 2
        assert result["groups"]["normal"] == 2


class TestPatternTimecourse:
    """时间序列模式识别测试"""

    def test_timecourse_before_after(self):
        """patient1_before / patient1_after - 直接测试 timecourse 函数"""
        samples = [
            "patient1_before",
            "patient1_after",
            "patient2_before",
            "patient2_after",
        ]
        result = pattern_timecourse(samples)
        assert result is not None
        assert result["method"] == "timecourse"
        assert result["confidence"] == 0.7
        assert "before" in result["groups"]
        assert "after" in result["groups"]

    def test_timecourse_day(self):
        """不同时间点的样本"""
        # 这些样本有不同的时间点 (day/week)
        samples = ["sample_day1", "sample_day2", "sample_week1", "sample_week2"]
        result = pattern_timecourse(samples)
        assert result is not None
        assert result["method"] == "timecourse"
        assert "day" in result["groups"]
        assert "week" in result["groups"]


class TestInferGroupsRobust:
    """健壮推断主函数测试"""

    def test_case1_prefix(self):
        """标准 prefix 模式"""
        samples = ["Con1", "Con2", "Con3", "GR1", "GR2", "GR3", "GS1", "GS2", "GS3"]
        result = infer_groups_robust(samples)

        assert result["method"] == "prefix"
        assert result["confidence"] == 0.95
        assert result["design"] == "multi-group"
        assert result["contrast_ready"] is True
        assert len(result["groups"]) == 3

    def test_case2_delimiter(self):
        """delimiter 模式"""
        samples = ["control_1", "control_2", "treated_1", "treated_2"]
        result = infer_groups_robust(samples)

        assert result["method"] == "delimiter"
        assert result["confidence"] == 0.85
        assert result["design"] == "binary"
        assert result["contrast_ready"] is True

    def test_case3_delimiter(self):
        """时间序列样本 - delimiter模式识别为patient配对"""
        samples = ["patient1_before", "patient1_after", "patient2_before", "patient2_after"]
        result = infer_groups_robust(samples)

        # delimiter 模式 confidence 更高，优先选择
        assert result["method"] == "delimiter"
        assert result["confidence"] == 0.85
        assert result["design"] == "binary"
        assert result["contrast_ready"] is True
        # 识别为 patient1/patient2 配对
        assert len(result["groups"]) == 2

    def test_case4_fallback(self):
        """无结构fallback"""
        samples = ["sampleA", "sampleB"]
        result = infer_groups_robust(samples)

        assert result["method"] == "fallback"
        assert result["confidence"] == 0.3
        assert result["design"] == "single-group"
        assert result["contrast_ready"] is False
        assert result["groups"][0]["name"] == "all"
        assert result["groups"][0]["n"] == 2

    def test_empty_input(self):
        """空输入"""
        result = infer_groups_robust([])
        assert result["method"] == "fallback"
        assert result["confidence"] == 0.3

    def test_single_group_no_contrast(self):
        """单组样本，无法做对比"""
        samples = ["Con1", "Con2", "Con3"]
        result = infer_groups_robust(samples)

        # 单组时 contrast_ready 应为 False
        assert result["contrast_ready"] is False


class TestBackwardCompatibility:
    """向后兼容测试"""

    def test_infer_groups_returns_list(self):
        """infer_groups 返回列表格式（兼容旧接口）"""
        samples = ["Con1", "Con2", "Treatment1", "Treatment2"]
        groups = infer_groups(samples)

        assert isinstance(groups, list)
        assert len(groups) == 2
        assert groups[0]["name"] in ["control", "Control"]
        assert groups[1]["name"] in ["treatment", "Treatment"]


class TestEdgeCases:
    """边界情况测试"""

    def test_mixed_case(self):
        """大小写混合"""
        samples = ["CON1", "con2", "Con3", "TR1", "tr2", "Tr3"]
        result = infer_groups_robust(samples)
        assert result["method"] == "prefix"
        assert len(result["groups"]) >= 2

    def test_with_underscores(self):
        """带下划线的样本名"""
        samples = ["GSM1_A", "GSM2_A", "GSM3_B", "GSM4_B"]
        result = infer_groups_robust(samples)
        # delimiter 模式应该能识别
        assert result["method"] in ["prefix", "delimiter"]

    def test_numeric_only(self):
        """纯数字样本名"""
        samples = ["1", "2", "3", "4"]
        result = infer_groups_robust(samples)
        assert result["method"] == "fallback"

    def test_patient_id_pattern(self):
        """patient1_pre / patient1_post"""
        samples = [
            "patient1_pre",
            "patient1_post",
            "patient2_pre",
            "patient2_post",
        ]
        result = infer_groups_robust(samples)
        # delimiter 模式识别为 patient 配对
        assert result["method"] == "delimiter"
        assert result["contrast_ready"] is True
        assert len(result["groups"]) == 2
