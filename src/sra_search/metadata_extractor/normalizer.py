"""字段标准化模块

将来自不同数据源的原始文本标准化为统一的术语：
- 物种名称标准化（各种别名 -> 标准学名）
- 器官名称标准化（自由文本 -> Uberon 术语）
- 疾病名称标准化（自由文本 -> DOID 术语）
- 平台名称标准化
"""
from __future__ import annotations

import re

# ============================================================
# 物种名称标准化
# ============================================================

# 常见物种别名映射 (别名 -> 标准学名)
_SPECIES_ALIASES: dict[str, str] = {
    # 人类
    "human": "Homo sapiens",
    "humans": "Homo sapiens",
    "homo sapiens": "Homo sapiens",
    "man": "Homo sapiens",
    "patient": "Homo sapiens",
    "patients": "Homo sapiens",
    "h. sapiens": "Homo sapiens",
    "hs": "Homo sapiens",
    # 小鼠
    "mouse": "Mus musculus",
    "mice": "Mus musculus",
    "mus musculus": "Mus musculus",
    "murine": "Mus musculus",
    "m. musculus": "Mus musculus",
    "mm": "Mus musculus",
    "laboratory mouse": "Mus musculus",
    "house mouse": "Mus musculus",
    # 大鼠
    "rat": "Rattus norvegicus",
    "rats": "Rattus norvegicus",
    "rattus norvegicus": "Rattus norvegicus",
    "r. norvegicus": "Rattus norvegicus",
    # 斑马鱼
    "zebrafish": "Danio rerio",
    "danio rerio": "Danio rerio",
    "d. rerio": "Danio rerio",
    # 果蝇
    "fruit fly": "Drosophila melanogaster",
    "drosophila melanogaster": "Drosophila melanogaster",
    "drosophila": "Drosophila melanogaster",
    "d. melanogaster": "Drosophila melanogaster",
    # 线虫
    "c. elegans": "Caenorhabditis elegans",
    "caenorhabditis elegans": "Caenorhabditis elegans",
    "nematode": "Caenorhabditis elegans",
    # 猴
    "macaque": "Macaca mulatta",
    "rhesus macaque": "Macaca mulatta",
    "macaca mulatta": "Macaca mulatta",
    "rhesus monkey": "Macaca mulatta",
    "monkey": "Macaca mulatta",
    "primates": "Macaca mulatta",
    # 狗
    "dog": "Canis lupus familiaris",
    "canis familiaris": "Canis lupus familiaris",
    # 猪
    "pig": "Sus scrofa domesticus",
    "porcine": "Sus scrofa domesticus",
    # 兔
    "rabbit": "Oryctolagus cuniculus",
    # 鸡
    "chicken": "Gallus gallus",
    "gallus gallus": "Gallus gallus",
    # 仓鼠
    "hamster": "Mesocricetus auratus",
    # 非洲爪蟾
    "xenopus": "Xenopus laevis",
    "xenopus laevis": "Xenopus laevis",
}

# 物种缩写 (缩写 -> 标准学名)
_SPECIES_ABBREVS: dict[str, str] = {
    "H.sapiens": "Homo sapiens",
    "M.musculus": "Mus musculus",
    "R.norvegicus": "Rattus norvegicus",
}


def normalize_organism(text: str) -> str:
    """标准化物种名称

    Args:
        text: 原始物种名称（可能是别名、缩写、学名）

    Returns:
        标准学名（如 "Homo sapiens"），无法识别则返回原始文本
    """
    if not text:
        return ""

    cleaned = text.strip()

    # 1. 精确匹配别名
    lower = cleaned.lower()
    if lower in _SPECIES_ALIASES:
        return _SPECIES_ALIASES[lower]

    # 2. 缩写匹配
    if cleaned in _SPECIES_ABBREVS:
        return _SPECIES_ABBREVS[cleaned]

    # 3. 已经是标准学名格式 (首字母大写 + 空格 + 小写)
    if re.match(r"^[A-Z][a-z]+ [a-z]+$", cleaned):
        return cleaned

    # 4. 常见模式: "Homo sapiens (human)"
    match = re.match(r"^([A-Z][a-z]+ [a-z]+)\s+\(.*\)$", cleaned)
    if match:
        return match.group(1)

    return cleaned


# ============================================================
# 器官名称标准化
# ============================================================

# 器官别名映射 (别名 -> 标准术语)
_ORGAN_ALIASES: dict[str, str] = {
    "bladder": "urinary bladder",
    "urinary bladder": "urinary bladder",
    "liver": "liver",
    "hepatic": "liver",
    "kidney": "kidney",
    "renal": "kidney",
    "lung": "lung",
    "pulmonary": "lung",
    "lungs": "lung",
    "heart": "heart",
    "cardiac": "heart",
    "brain": "brain",
    "cerebral": "brain",
    "cortex": "cerebral cortex",
    "cerebral cortex": "cerebral cortex",
    "hippocampus": "hippocampus",
    "skin": "skin",
    "dermal": "skin",
    "intestine": "intestine",
    "intestinal": "intestine",
    "colon": "colon",
    "colorectal": "colon",
    "small intestine": "small intestine",
    "large intestine": "large intestine",
    "blood": "blood",
    "peripheral blood": "peripheral blood",
    "pbmc": "peripheral blood mononuclear cell",
    "peripheral blood mononuclear cell": "peripheral blood mononuclear cell",
    "pbmcs": "peripheral blood mononuclear cell",
    "bone marrow": "bone marrow",
    "spleen": "spleen",
    "splenic": "spleen",
    "pancreas": "pancreas",
    "pancreatic": "pancreas",
    "islet": "pancreatic islet",
    "islets": "pancreatic islet",
    "pancreatic islet": "pancreatic islet",
    "islets of langerhans": "pancreatic islet",
    "stomach": "stomach",
    "gastric": "stomach",
    "breast": "breast",
    "mammary gland": "breast",
    "prostate": "prostate",
    "prostatic": "prostate",
    "ovary": "ovary",
    "ovarian": "ovary",
    "testis": "testis",
    "testicular": "testis",
    "uterus": "uterus",
    "uterine": "uterus",
    "placenta": "placenta",
    "retina": "retina",
    "retinal": "retina",
    "eye": "eye",
    "ocular": "eye",
    "skeletal muscle": "skeletal muscle",
    "muscle": "skeletal muscle",
    "cartilage": "cartilage",
    "bone": "bone",
    "adipose": "adipose tissue",
    "fat": "adipose tissue",
    "thyroid": "thyroid gland",
    "thyroid gland": "thyroid gland",
    "lymph node": "lymph node",
    "lymph": "lymph node",
    "thymus": "thymus",
    "esophagus": "esophagus",
    "esophageal": "esophagus",
    "gallbladder": "gallbladder",
    "adrenal gland": "adrenal gland",
    "adrenal": "adrenal gland",
    "salivary gland": "salivary gland",
    "spinal cord": "spinal cord",
    "trachea": "trachea",
    "nasal": "nasal cavity",
    "nasal cavity": "nasal cavity",
}


def normalize_organ(text: str) -> str:
    """标准化器官名称

    Args:
        text: 原始器官名称

    Returns:
        标准术语，无法识别则返回原始文本
    """
    if not text:
        return ""

    cleaned = text.strip().lower()
    return _ORGAN_ALIASES.get(cleaned, text.strip())


# ============================================================
# 疾病名称标准化
# ============================================================

_DISEASE_ALIASES: dict[str, str] = {
    # 癌症
    "cancer": "cancer",
    "carcinoma": "carcinoma",
    "tumor": "tumor",
    "tumour": "tumor",
    "malignancy": "malignancy",
    "malignant": "malignancy",
    "oncology": "cancer",
    "bladder cancer": "bladder cancer",
    "bladder carcinoma": "bladder cancer",
    "urothelial carcinoma": "urothelial carcinoma",
    "transitional cell carcinoma": "urothelial carcinoma",
    "liver cancer": "liver cancer",
    "hepatocellular carcinoma": "hepatocellular carcinoma",
    "hcc": "hepatocellular carcinoma",
    "lung cancer": "lung cancer",
    "non-small cell lung cancer": "non-small cell lung cancer",
    "nsclc": "non-small cell lung cancer",
    "small cell lung cancer": "small cell lung cancer",
    "sclc": "small cell lung cancer",
    "breast cancer": "breast cancer",
    "colorectal cancer": "colorectal cancer",
    "colon cancer": "colorectal cancer",
    "crc": "colorectal cancer",
    "prostate cancer": "prostate cancer",
    "gastric cancer": "gastric cancer",
    "stomach cancer": "gastric cancer",
    "pancreatic cancer": "pancreatic cancer",
    "pancreatic ductal adenocarcinoma": "pancreatic ductal adenocarcinoma",
    "pdac": "pancreatic ductal adenocarcinoma",
    "kidney cancer": "kidney cancer",
    "renal cell carcinoma": "renal cell carcinoma",
    "rcc": "renal cell carcinoma",
    "glioblastoma": "glioblastoma",
    "gbm": "glioblastoma",
    "melanoma": "melanoma",
    "leukemia": "leukemia",
    "lymphoma": "lymphoma",
    "ovarian cancer": "ovarian cancer",
    "cervical cancer": "cervical cancer",
    "endometrial cancer": "endometrial cancer",
    "brain cancer": "brain cancer",
    # 代谢疾病
    "diabetes": "diabetes mellitus",
    "diabetes mellitus": "diabetes mellitus",
    "t2d": "type 2 diabetes mellitus",
    "type 2 diabetes": "type 2 diabetes mellitus",
    "type 2 diabetes mellitus": "type 2 diabetes mellitus",
    "t1d": "type 1 diabetes mellitus",
    "type 1 diabetes": "type 1 diabetes mellitus",
    "type 1 diabetes mellitus": "type 1 diabetes mellitus",
    "nafld": "non-alcoholic fatty liver disease",
    "non-alcoholic fatty liver disease": "non-alcoholic fatty liver disease",
    "nash": "non-alcoholic steatohepatitis",
    "non-alcoholic steatohepatitis": "non-alcoholic steatohepatitis",
    "mash": "metabolic dysfunction-associated steatohepatitis",
    "metabolic dysfunction-associated steatohepatitis": "metabolic dysfunction-associated steatohepatitis",
    "obesity": "obesity",
    # 心血管
    "heart disease": "heart disease",
    "coronary artery disease": "coronary artery disease",
    "chd": "coronary artery disease",
    "cad": "coronary artery disease",
    "heart failure": "heart failure",
    "hypertension": "hypertension",
    # 神经
    "alzheimer": "Alzheimer disease",
    "alzheimer's disease": "Alzheimer disease",
    "alzheimer disease": "Alzheimer disease",
    "parkinson": "Parkinson disease",
    "parkinson's disease": "Parkinson disease",
    "parkinson disease": "Parkinson disease",
    # 免疫
    "copd": "chronic obstructive pulmonary disease",
    "chronic obstructive pulmonary disease": "chronic obstructive pulmonary disease",
    "asthma": "asthma",
    "arthritis": "arthritis",
    "rheumatoid arthritis": "rheumatoid arthritis",
    "lupus": "systemic lupus erythematosus",
    "systemic lupus erythematosus": "systemic lupus erythematosus",
    "ibd": "inflammatory bowel disease",
    "inflammatory bowel disease": "inflammatory bowel disease",
    "crohn": "Crohn disease",
    "crohn's disease": "Crohn disease",
    "ulcerative colitis": "ulcerative colitis",
    # 感染
    "covid-19": "COVID-19",
    "sars-cov-2": "COVID-19",
    "hiv": "HIV infection",
    "hiv infection": "HIV infection",
    "aids": "AIDS",
    "hepatitis b": "hepatitis B",
    "hepatitis c": "hepatitis C",
    "tuberculosis": "tuberculosis",
    "tb": "tuberculosis",
    "malaria": "malaria",
    # 肾脏
    "ckd": "chronic kidney disease",
    "chronic kidney disease": "chronic kidney disease",
    "akd": "acute kidney disease",
    "acute kidney injury": "acute kidney injury",
    # 纤维化
    "fibrosis": "fibrosis",
    "liver fibrosis": "liver fibrosis",
    "pulmonary fibrosis": "pulmonary fibrosis",
    "ipf": "idiopathic pulmonary fibrosis",
    "idiopathic pulmonary fibrosis": "idiopathic pulmonary fibrosis",
}


def normalize_disease(text: str) -> str:
    """标准化疾病名称

    Args:
        text: 原始疾病名称

    Returns:
        标准术语，无法识别则返回原始文本
    """
    if not text:
        return ""

    cleaned = text.strip()

    # 精确匹配
    lower = cleaned.lower()
    if lower in _DISEASE_ALIASES:
        return _DISEASE_ALIASES[lower]

    # 多词匹配（尝试从左到右递减长度）
    words = lower.split()
    for length in range(min(len(words), 5), 0, -1):
        phrase = " ".join(words[:length])
        if phrase in _DISEASE_ALIASES:
            return _DISEASE_ALIASES[phrase]

    return cleaned


# ============================================================
# 平台名称标准化
# ============================================================

_PLATFORM_ALIASES: dict[str, str] = {
    "illumina": "Illumina",
    "illumina hiseq": "Illumina HiSeq",
    "illumina novaseq": "Illumina NovaSeq",
    "illumina nextseq": "Illumina NextSeq",
    "illumina genome analyzer": "Illumina Genome Analyzer",
    "illumina ga": "Illumina Genome Analyzer",
    "illumina miseq": "Illumina MiSeq",
    "hiseq": "Illumina HiSeq",
    "novaseq": "Illumina NovaSeq",
    "nextseq": "Illumina NextSeq",
    "miseq": "Illumina MiSeq",
    "10x genomics chromium": "10x Genomics Chromium",
    "10x chromium": "10x Genomics Chromium",
    "10x genomics visium": "10x Genomics Visium",
    "10x visium": "10x Genomics Visium",
    "10x genomics xenium": "10x Genomics Xenium",
    "10x xenium": "10x Genomics Xenium",
    "10x genomics": "10x Genomics",
    "affymetrix": "Affymetrix",
    "affymetrix genechip": "Affymetrix GeneChip",
    "genechip": "Affymetrix GeneChip",
    "agilent": "Agilent",
    "agilent microarray": "Agilent Microarray",
    "pacbio": "PacBio",
    "pac biosystems": "PacBio",
    "oxford nanopore": "Oxford Nanopore",
    "nanopore": "Oxford Nanopore",
    "ont": "Oxford Nanopore",
    "bd rhapsody": "BD Rhapsody",
    "smart-seq": "Smart-seq",
    "drop-seq": "Drop-seq",
    "celseq": "CEL-Seq",
    "macsima": "MACSima",
    "nanostring geomx": "NanoString GeoMx",
    "geomx": "NanoString GeoMx",
    "merfish": "MERFISH",
    "slide-seq": "Slide-seq",
    "stereo-seq": "Stereo-seq",
}


def normalize_platform(text: str) -> str:
    """标准化实验平台名称

    Args:
        text: 原始平台名称

    Returns:
        标准化名称，无法识别则返回原始文本
    """
    if not text:
        return ""

    cleaned = text.strip()
    lower = cleaned.lower()

    # 精确匹配
    if lower in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[lower]

    # 包含匹配（取最长的）
    best_match = cleaned
    best_len = 0
    for alias, standard in sorted(_PLATFORM_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in lower and len(alias) > best_len:
            best_match = standard
            best_len = len(alias)

    return best_match


# ============================================================
# 统一标准化接口
# ============================================================

def normalize_dataset_fields(
    organism: str = "",
    disease: str = "",
    organ: str = "",
    platform: str = "",
) -> dict[str, str]:
    """统一标准化数据集字段

    Args:
        organism: 原始物种名称
        disease: 原始疾病名称
        organ: 原始器官名称
        platform: 原始平台名称

    Returns:
        标准化后的字段字典
    """
    return {
        "organism": normalize_organism(organism),
        "disease": normalize_disease(disease),
        "organ": normalize_organ(organ),
        "platform": normalize_platform(platform),
    }


def extract_disease_from_text(text: str) -> str | None:
    """从自由文本中提取标准化的疾病名称

    扫描文本中匹配的已知疾病术语，返回最长的匹配。

    Args:
        text: 自由文本（标题、摘要等）

    Returns:
        标准化的疾病名称，未找到则返回 None
    """
    if not text:
        return None

    lower = text.lower()
    best_match = None
    best_len = 0

    # 按长度降序排列，优先匹配更长的短语
    for alias, standard in sorted(_DISEASE_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in lower and len(alias) > best_len:
            best_match = standard
            best_len = len(alias)

    return best_match


def extract_organ_from_text(text: str) -> str | None:
    """从自由文本中提取标准化的器官名称

    Args:
        text: 自由文本

    Returns:
        标准化的器官名称，未找到则返回 None
    """
    if not text:
        return None

    lower = text.lower()
    best_match = None
    best_len = 0

    for alias, standard in sorted(_ORGAN_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in lower and len(alias) > best_len:
            best_match = standard
            best_len = len(alias)

    return best_match
