# PLACEHOLDER - sera remplacé par Dev C (feature/fusion)
# Interface à respecter : fuse(contributions: list[str]) -> str

def fuse(contributions: list[str]) -> str:
    """Version minimale : retourne la contribution la plus longue"""
    if not contributions:
        return ""
    return max(contributions, key=len)
