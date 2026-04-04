"""生物体名称映射

参考 ArcInstitute/SRAgent — SRAgent/organisms.py
支持 50+ 种常见实验生物体的名称规范化。

使用方法::

    from sra_search.data.organisms import normalize_organism, ORGANISM_MAP

    sci_name = normalize_organism("mouse")  # → "Mus musculus"
    sci_name = normalize_organism("human")  # → "Homo sapiens"
"""
from __future__ import annotations

from typing import Dict, List, Optional

# ──────────────────────────────────────────────
# 生物体映射表：{常用名: 学名}
# ──────────────────────────────────────────────

ORGANISM_MAP: Dict[str, str] = {
    # 哺乳动物
    "human": "Homo sapiens",
    "homo sapiens": "Homo sapiens",
    "mouse": "Mus musculus",
    "mice": "Mus musculus",
    "mus musculus": "Mus musculus",
    "rat": "Rattus norvegicus",
    "rattus norvegicus": "Rattus norvegicus",
    "macaque": "Macaca mulatta",
    "rhesus macaque": "Macaca mulatta",
    "macaca mulatta": "Macaca mulatta",
    "marmoset": "Callithrix jacchus",
    "callithrix jacchus": "Callithrix jacchus",
    "horse": "Equus caballus",
    "equus caballus": "Equus caballus",
    "dog": "Canis lupus familiaris",
    "canis lupus": "Canis lupus familiaris",
    "bovine": "Bos taurus",
    "cattle": "Bos taurus",
    "cow": "Bos taurus",
    "bos taurus": "Bos taurus",
    "sheep": "Ovis aries",
    "ovis aries": "Ovis aries",
    "pig": "Sus scrofa",
    "swine": "Sus scrofa",
    "sus scrofa": "Sus scrofa",
    "rabbit": "Oryctolagus cuniculus",
    "oryctolagus cuniculus": "Oryctolagus cuniculus",
    "naked mole-rat": "Heterocephalus glaber",
    "heterocephalus glaber": "Heterocephalus glaber",
    "chimpanzee": "Pan troglodytes",
    "pan troglodytes": "Pan troglodytes",
    "gorilla": "Gorilla gorilla",
    "gorilla gorilla": "Gorilla gorilla",
    "cat": "Felis catus",
    "felis catus": "Felis catus",
    "bonobo": "Pan paniscus",
    "pan paniscus": "Pan paniscus",
    "green monkey": "Chlorocebus aethiops",
    "vervet": "Chlorocebus aethiops",
    "chlorocebus aethiops": "Chlorocebus aethiops",
    "opossum": "Monodelphis domestica",
    "monodelphis domestica": "Monodelphis domestica",
    "goat": "Capra hircus",
    "capra hircus": "Capra hircus",
    "alpaca": "Vicugna pacos",
    "vicugna pacos": "Vicugna pacos",
    "chinchilla": "Chinchilla lanigera",
    "chinchilla lanigera": "Chinchilla lanigera",
    "guinea pig": "Cavia porcellus",
    "cavia porcellus": "Cavia porcellus",
    "hamster": "Mesocricetus auratus",
    "golden hamster": "Mesocricetus auratus",
    "mesocricetus auratus": "Mesocricetus auratus",
    "hedgehog": "Erinaceus europaeus",
    "erinaceus europaeus": "Erinaceus europaeus",
    "mink": "Neovison vison",
    "neovison vison": "Neovison vison",
    "pangolin": "Manis javanica",
    "manis javanica": "Manis javanica",
    "platypus": "Ornithorhynchus anatinus",
    "ornithorhynchus anatinus": "Ornithorhynchus anatinus",
    "ferret": "Mustela putorius furo",
    "mustela putorius": "Mustela putorius furo",
    "tree shrew": "Tupaia belangeri",
    "tupaia belangeri": "Tupaia belangeri",
    # 鸟类
    "chicken": "Gallus gallus",
    "gallus gallus": "Gallus gallus",
    "zebrafinch": "Taeniopygia guttata",
    "taeniopygia guttata": "Taeniopygia guttata",
    "goose": "Anser cygnoides",
    "anser cygnoides": "Anser cygnoides",
    "duck": "Anas platyrhynchos",
    "anas platyrhynchos": "Anas platyrhynchos",
    # 爬行类
    "turtle": "Trachemys scripta",
    "trachemys scripta": "Trachemys scripta",
    # 两栖类
    "frog": "Xenopus tropicalis",
    "xenopus": "Xenopus tropicalis",
    "xenopus tropicalis": "Xenopus tropicalis",
    "axolotl": "Ambystoma mexicanum",
    "ambystoma mexicanum": "Ambystoma mexicanum",
    # 鱼类
    "zebrafish": "Danio rerio",
    "danio rerio": "Danio rerio",
    "salmon": "Salmo salar",
    "salmo salar": "Salmo salar",
    "stickleback": "Gasterosteus aculeatus",
    "gasterosteus aculeatus": "Gasterosteus aculeatus",
    # 无脊椎动物
    "fly": "Drosophila melanogaster",
    "drosophila": "Drosophila melanogaster",
    "fruit fly": "Drosophila melanogaster",
    "drosophila melanogaster": "Drosophila melanogaster",
    "worm": "Caenorhabditis elegans",
    "c. elegans": "Caenorhabditis elegans",
    "caenorhabditis elegans": "Caenorhabditis elegans",
    "mosquito": "Anopheles gambiae",
    "anopheles gambiae": "Anopheles gambiae",
    "blood fluke": "Schistosoma mansoni",
    "schistosoma mansoni": "Schistosoma mansoni",
    # 植物
    "arabidopsis": "Arabidopsis thaliana",
    "thale cress": "Arabidopsis thaliana",
    "arabidopsis thaliana": "Arabidopsis thaliana",
    "rice": "Oryza sativa",
    "oryza sativa": "Oryza sativa",
    "tomato": "Solanum lycopersicum",
    "solanum lycopersicum": "Solanum lycopersicum",
    "corn": "Zea mays",
    "maize": "Zea mays",
    "zea mays": "Zea mays",
    # 微生物/其他
    "metagenome": "metagenome",
    "metagenomics": "metagenome",
}

# 反向映射：{学名: 常用名}
_REVERSE_MAP: Dict[str, str] = {v.lower(): k for k, v in ORGANISM_MAP.items()}


def normalize_organism(name: str) -> Optional[str]:
    """将生物体名称规范化为学名

    Args:
        name: 任意格式的生物体名称（大小写不敏感）

    Returns:
        对应的学名，或 None（未识别）

    Example::

        normalize_organism("mouse")  # → "Mus musculus"
        normalize_organism("HUMAN")  # → "Homo sapiens"
    """
    if not name:
        return None
    key = name.lower().strip()
    return ORGANISM_MAP.get(key)


def to_entrez_organism_filter(
    organisms: List[str],
    quoted: bool = True,
) -> str:
    """将生物体列表转为 Entrez 查询过滤条件

    Args:
        organisms: 生物体名称列表（常用名或学名）
        quoted: 是否用引号包裹学名

    Returns:
        Entrez 查询字符串片段，如::

            ("Homo sapiens"[Organism] OR "Mus musculus"[Organism])

    Example::

        to_entrez_organism_filter(["human", "mouse"])
        # → '("Homo sapiens"[Organism] OR "Mus musculus"[Organism])'
    """
    sci_names = []
    for org in organisms:
        sci = normalize_organism(org) or org
        if quoted:
            sci_names.append(f'"{sci}"[Organism]')
        else:
            sci_names.append(f"{sci}[Organism]")

    if not sci_names:
        return ""
    if len(sci_names) == 1:
        return sci_names[0]
    return "(" + " OR ".join(sci_names) + ")"


def list_supported_organisms() -> List[str]:
    """返回所有支持的常用名列表（去重排序）"""
    # 仅返回不含空格或连字符开头的主要名称
    names = sorted(set(
        k for k in ORGANISM_MAP.keys()
        if not k[0].isupper()  # 排除学名（学名首字母大写）
    ))
    return names
