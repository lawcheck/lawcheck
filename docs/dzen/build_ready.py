"""Markdown статей → текст, который можно вставить в редактор Дзена.

Редактор Дзена markdown не понимает: «##» и «[текст](ссылка)» попадут
в публикацию буквально. Поэтому:

- заголовки разделов остаются отдельной строкой без решёток (в редакторе
  выделяются как «Заголовок» через панель);
- ссылки разворачиваются в «текст — URL», Дзен подхватывает голый URL сам;
- таблицы разворачиваются в список «строка: значение» – таблицы в Дзене
  выглядят плохо и ломаются на телефоне;
- жирный «**…**» снимается, его ставят руками в паре мест.

    .venv/bin/python docs/dzen/build_ready.py
"""
import re
from pathlib import Path

SRC = Path(__file__).parent
OUT = SRC / "ready"


def table_to_list(block: list[str]) -> list[str]:
    rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in block]
    rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
    if not rows:
        return []
    head, *body = rows
    out = []
    for r in body:
        first, rest = r[0], r[1:]
        pairs = [f"{h} — {v}" for h, v in zip(head[1:], rest)
                 if v and v not in {"–", "—", "-"}]
        out.append(f"• {first}: " + ", ".join(pairs) if pairs else f"• {first}")
    return out


def convert(md: str) -> str:
    lines = md.splitlines()
    title = lines[0].lstrip("# ").strip()
    body = "\n".join(lines[1:])
    body = re.sub(r"^\s*_[^\n]*_\s*\n", "", body.lstrip("\n"))
    body = re.sub(r"^\s*-{3,}\s*\n", "", body.lstrip("\n"))

    out: list[str] = []
    buf: list[str] = []
    for line in body.splitlines():
        if line.strip().startswith("|"):
            buf.append(line)
            continue
        if buf:
            out += table_to_list(buf)
            buf = []
        if re.match(r"^\s*-{3,}\s*$", line):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 — \2", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        out.append(line)
    if buf:
        out += table_to_list(buf)

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"{title}\n\n{text}\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for f in sorted(SRC.glob("0[0-9]-*.md")):
        res = convert(f.read_text(encoding="utf-8"))
        (OUT / f"{f.stem}.txt").write_text(res, encoding="utf-8")
        title = res.split("\n")[0]
        print(f"{f.stem}.txt  заголовок {len(title)} зн., всего {len(res)} зн.")


main()
