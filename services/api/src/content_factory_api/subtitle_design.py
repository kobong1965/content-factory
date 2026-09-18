"""Local caption design. Highlight only words actually present in the speech."""
import re

FONT_FAMILY = 'SimHei'
FONT_SIZE = 68  # Previous footage captions: 52 px; +30%, rounded to whole pixels.
KEYWORDS = ('弹力', '直筒', '九分', '长裤', '裤型', '版型', '裤脚', '裤口', '腰头', '搭配', '身高体重')


def caption_text(value: str) -> str:
    # Neutralize user-authored ASS controls before inserting our own color tags.
    text = value.replace('\\', '／').replace('{', '（').replace('}', '）').replace('\r', '').strip()
    lines = []
    for line in text.split('\n'):
        current = ''
        for part in re.split('(' + '|'.join(map(re.escape, KEYWORDS)) + ')', line):
            for token in ([part] if part in KEYWORDS else list(part)):
                if len(current) + len(token) > 12:
                    lines.append(current)
                    current = ''
                current += token
        if current:
            lines.append(current)
    text = r'\N'.join(lines)
    return re.sub('|'.join(map(re.escape, KEYWORDS)), lambda m: r'{\c&H00FFFF&}' + m.group() + r'{\rDefault}', text)
