import hashlib
import logging
import re

import yaml

from .config import IDENTIFIER, settings

log = logging.getLogger(__name__)


def catalog():
    root = (settings().config_root / "skill").resolve()
    result = {}
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            if not path.resolve().is_relative_to(root) or path.stat().st_size > 65536:
                raise ValueError()
            text = path.read_text(encoding="utf-8-sig")
            match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n([\s\S]+)\Z", text, re.S)
            if not match:
                raise ValueError()
            meta = yaml.safe_load(match[1])
            ident = path.parent.name
            if not isinstance(meta, dict) or meta.get("name") != ident:
                raise ValueError()
            if len(ident) > 64 or not re.fullmatch(IDENTIFIER, ident):
                raise ValueError()
            description = meta.get("description")
            if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
                raise ValueError()
            body = match[2].strip()
            if not body:
                raise ValueError()
            result[ident] = {
                "id": ident,
                "name": ident,
                "description": description.strip(),
                "body": body,
                "version": hashlib.sha256(text.encode()).hexdigest(),
                "default_enabled": ident in {"explain", "study-plan", "practice"},
            }
        except (OSError, ValueError, yaml.YAMLError):
            log.warning("invalid skill directory=%s", path.parent.name)
    return result


def public(skill):
    return {key: skill[key] for key in ("id", "name", "description", "default_enabled")}
