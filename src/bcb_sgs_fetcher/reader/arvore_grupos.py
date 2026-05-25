"""Parsers for the "árvore de grupos" navigation page."""

import re
from collections.abc import Generator

from bs4 import BeautifulSoup, Tag

from ..models import ArvoreGrupoLink, GrupoLink, ThemeNode


def _parse_grupo_link(href: str) -> dict[str, str]:
    """Extract id and name from a ``selecionarGrupoLink`` javascript href."""
    pattern = r"javascript:selecionarGrupoLink\((\d+), '(.+)'\);"
    m = re.match(pattern, href)
    if not m:
        raise ValueError(f"Unexpected grupo link: {href!r}")
    grupo_id, grupo_nome = m.groups()
    return {"grupo_id": grupo_id, "grupo_nome": grupo_nome}


def _walk_level_items(
    li_list: list[Tag],
) -> Generator[tuple[str, str | None, Tag], None, None]:
    for li in li_list:
        span = li.find("span", recursive=False)
        if not span:
            list_a = li.find_all("a", recursive=False)
            if len(list_a) == 2:  # item com link de grupo e subitens
                a = list_a[1]
            else:
                a = list_a[0]
            a_href = a.get("href")
            span = a.find("span", recursive=False)
            yield span.text.strip(), a_href, li
            continue
        yield span.text.strip(), None, li


def extract_theme_nodes(
    li_list: list[Tag],
    level: int = 1,
    chain: list[str] | None = None,
) -> Generator[ThemeNode, None, None]:
    """Recursively extract theme nodes from a list of ``<li>`` tags."""
    if chain is None:
        chain = []
    for name, href, li in _walk_level_items(li_list):
        link_metadata = _parse_grupo_link(href) if href else None
        if link_metadata:
            yield ThemeNode(
                theme_chain=chain + [name],
                link_id=link_metadata.get("grupo_id"),
                link_name=link_metadata.get("grupo_nome"),
                level=level,
            )
        sublist = li.select(f"div > ul.lista{level + 1} > li")
        if sublist:
            yield from extract_theme_nodes(sublist, level + 1, chain + [name])


def _parse_arvore_link(href: str) -> dict[str, str]:
    """Parse the ``montarArvoreGrupos`` javascript href."""
    args = re.search(
        r"javascript:parent\.montarArvoreGrupos\('(.*)',(\d+),(\d+),(.*)\)",
        href,
    )
    if not args:
        raise ValueError(f"Unexpected árvore link: {href!r}")
    _, hd_oid, hd_seq, _ = args.groups()
    return {
        "hd_oid_grupo_selecionado": hd_oid,
        "hd_seq_grupo_selecionado": hd_seq,
    }


def extract_arvore_grupos(table: Tag) -> list[ArvoreGrupoLink]:
    """Extract metadata for top-level groups from the árvore-grupos table."""
    table = table.__copy__()
    links: list[ArvoreGrupoLink] = []
    for td in table.select("td"):
        a = td.select_one("a")
        if a is None:
            continue
        a_text = re.sub(r"\s+", " ", a.text.strip())
        if a_text in ("Tabelas especiais", "Expectativas do mercado"):
            continue
        a_href = a["href"]
        link_metadata = _parse_arvore_link(a_href)
        a.decompose()
        subtext = re.sub(r"\s+", " ", td.text.strip())
        links.append(
            ArvoreGrupoLink(
                hd_oid_grupo_selecionado=link_metadata["hd_oid_grupo_selecionado"],
                hd_seq_grupo_selecionado=link_metadata["hd_seq_grupo_selecionado"],
                nome=a_text,
                subtext=subtext or None,
            )
        )
    return links


def extract_grupo_links(soup: BeautifulSoup) -> list[GrupoLink]:
    """Extract every ``selecionarGrupoLink`` link from a page."""
    out: list[GrupoLink] = []
    match_pattern = re.compile(r"javascript:selecionarGrupoLink\((\d+), '(.+)'\);")
    for a in soup.select("a"):
        href = a.get("href")
        if not href:
            continue
        if m := match_pattern.match(href):
            grupo_id, grupo_nome = m.groups()
            out.append(GrupoLink(grupo_id=grupo_id, grupo_nome=grupo_nome))
    return out
