"""Optional model layer. Credentials stay on the local server."""

import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT_RANGES = {
    "Tamil": (0xB80, 0xBFF),
    "Hindi": (0x900, 0x97F),
    "Marathi": (0x900, 0x97F),
    "Gujarati": (0xA80, 0xAFF),
    "Telugu": (0xC00, 0xC7F),
    "Kannada": (0xC80, 0xCFF),
    "Malayalam": (0xD00, 0xD7F),
    "Bengali": (0x980, 0x9FF),
    "Assamese": (0x980, 0x9FF),
    "Odia": (0xB00, 0xB7F),
}


def uses_script(text, language):
    a, b = SCRIPT_RANGES[language]
    return any(a <= ord(c) <= b for c in text)


class Model:
    def __init__(self, state_dir):
        self.mode = "Guided conversation"
        self.binary = None
        self.key = (
            os.environ.get("OPENAI_API_KEY")
            if os.environ.get("MARICO_MODEL") == "openai"
            else None
        )
        self.cloud_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        if self.key:
            self.mode = f"OpenAI · {self.cloud_model}"
            return
        if (
            os.environ.get("MARICO_MODEL") in ("off", "openai")
            or platform.system() != "Darwin"
            or not shutil.which("swiftc")
        ):
            return
        source = ROOT / "OnDevice.swift"
        binary = Path(state_dir) / "retailer-language"
        try:
            if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
                subprocess.run(
                    [
                        "swiftc",
                        "-parse-as-library",
                        "-module-cache-path",
                        str(Path(state_dir) / "swift-cache"),
                        str(source),
                        "-o",
                        str(binary),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
            subprocess.run(
                [str(binary), "--check"], check=True, capture_output=True, timeout=5
            )
            self.binary = str(binary)
            self.mode = "Apple on-device model"
        except (OSError, subprocess.SubprocessError):
            pass

    @property
    def enabled(self):
        return bool(self.binary or self.key)

    def ask(self, instructions, text):
        try:
            if self.binary:
                job = json.dumps(
                    {"instructions": instructions, "input": text}, ensure_ascii=False
                )
                return subprocess.run(
                    [self.binary],
                    input=job,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=25,
                ).stdout.strip()
            if self.key:
                body = {
                    "model": self.cloud_model,
                    "messages": [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0,
                }
                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    json.dumps(body).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + self.key,
                    },
                )
                with urllib.request.urlopen(req, timeout=22) as response:
                    return json.load(response)["choices"][0]["message"][
                        "content"
                    ].strip()
        except (OSError, subprocess.SubprocessError, KeyError, IndexError, ValueError):
            pass
        return ""

    def understand(self, utterance):
        """Free paraphrases may propose a draft. Only literal retailer consent can commit."""
        return self.ask(
            "You interpret a retailer's spoken words. Translate or rewrite the utterance as short, plain English for an order clerk. Use forms such as 'send 6 pieces of [exact product and pack]', 'change it to 5 pieces', 'price of [product]', or 'recommend a product' only if they express the same intent. Preserve negatives, questions, numbers, brands, units and ambiguity. Never invent missing units, products or quantities. Return only the paraphrase.",
            utterance,
        )

    def translate(self, text, language):
        if language == "English":
            return text
        result = self.ask(
            f"Translate this retail call into natural {language} in its native script. Copy every placeholder of the form [[0]] verbatim exactly once. Preserve meaning, questions and negation. Only output the translation.",
            text,
        )
        if not result or not uses_script(result, language):
            return ""
        if sorted(re.findall(r"\[\[\d+\]\]", text)) != sorted(
            re.findall(r"\[\[\d+\]\]", result)
        ):
            return ""
        return result
