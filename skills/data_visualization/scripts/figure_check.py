import base64
import json
import re
from pathlib import Path
from core.prompts.prompts import FIGURE_EVALUATION_SYSTEM_PROMPT
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv

load_dotenv()

# Pydantic output schema
class FigureFeedback(BaseModel):
    """Structured, validated output from the figure evaluation LLM."""

    Readability: str = Field(
        default="No feedback provided.",
        description="Feedback on font sizes, text overlap, contrast, and ink economy.",
    )
    Panel_Arrangement: str = Field(
        default="No feedback provided.",
        alias="Panel Arrangement",
        description="Feedback on panel ordering, spacing, sizing, and panel labels.",
    )
    Axis_Labels: str = Field(
        default="No feedback provided.",
        alias="Axis Labels",
        description="Feedback on axis label clarity, units, tick legibility, and spines.",
    )
    Legend: str = Field(
        default="No feedback provided.",
        description="Feedback on legend frame, placement, font size, and redundancy.",
    )
    Color: str = Field(
        default="No feedback provided.",
        description="Feedback on colorblind safety, colormap choice, and colorbar labels.",
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _strip_empty_strings(cls, values: dict) -> dict:
        """Replace blank strings with the default fallback message."""
        fallback = "No feedback provided."
        return {
            k: (v if isinstance(v, str) and v.strip() else fallback)
            for k, v in values.items()
        }

    def to_dict(self) -> dict[str, str]:
        """Return feedback keyed by the original five dimension names."""
        return {
            "Readability":       self.Readability,
            "Panel Arrangement": self.Panel_Arrangement,
            "Axis Labels":       self.Axis_Labels,
            "Legend":            self.Legend,
            "Color":             self.Color,
        }



# Helper functions

def _encode_image(figure_path: str) -> tuple[str, str]:
    """Read an image file and return (base64_data, media_type)."""
    path = Path(figure_path)
    suffix = path.suffix.lower()
    media_type_map = {
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif":  "image/gif",
        ".webp": "image/webp",
        ".svg":  "image/svg+xml",
    }
    media_type = media_type_map.get(suffix, "image/png")
    with open(path, "rb") as fh:
        data = base64.standard_b64encode(fh.read()).decode("utf-8")
    return data, media_type


def _call_llm(
    image_data: str,
    media_type: str,
    system_prompt: str,
    llm: str = "gpt-5.2",
) -> str:
    """
    Call the LLM via LangChain's ChatOpenAI with a vision-capable message.

    Reads OPENAI_API_KEY from the environment (standard LangChain behaviour).

    Args:
    ----------
    image_data : base64-encoded image string
    media_type : MIME type, e.g. "image/png"
    system : system prompt text
    model : OpenAI model identifier
    temperature : sampling temperature (0 = deterministic)
    max_tokens : maximum tokens in the response

    Returns:
    -------
    str — the assistant's raw reply
    """
    llm = ChatOpenAI(model=llm)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=[
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{image_data}",
                    "detail": "high",
                },
            },
            {
                "type": "text",
                "text": (
                    "Evaluate this scientific figure according to the publication standards in your system prompt. Return ONLY the JSON object described in the output format."
                ),
            },
        ]),
    ]

    response = llm.invoke(messages)
    return response.content


def _parse_with_pydantic(raw: str) -> FigureFeedback:
    """
    Parse and validate raw LLM output into a FigureFeedback Pydantic model.
    """
    # Strip markdown code fences
    clean = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()

    data: dict = {}

    # Direct parse
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Brace-extraction fallback
    if not data:
        start = clean.find("{")
        end   = clean.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(clean[start : end + 1])
            except json.JSONDecodeError:
                pass

    # Validate with Pydantic
    return FigureFeedback.model_validate(data)


# Main tool
def figure_check(
    figure_path: str,
    llm: str = "gpt-5.2",
) -> dict[str, str]:
    """
    Evaluate a figure image on five publication-quality dimensions.

    Args
    ----------
    figure_path : str
        Absolute or relative path to the figure file
        (PNG, JPG, JPEG, GIF, WEBP, or SVG).
    model : str
        OpenAI model identifier passed to LangChain's ChatOpenAI.
    temperature : float
        Sampling temperature (0 = deterministic).
    max_tokens : int
        Maximum tokens in the LLM response.

    Returns
    -------
    dict[str, str]
        A dictionary with exactly five keys:
        "Readability", "Panel Arrangement", "Axis Labels", "Legend", "Color"
    """
    # Load image
    path = Path(figure_path)
    if not path.exists():
        raise FileNotFoundError(f"Figure not found: {figure_path}")

    image_data, media_type = _encode_image(figure_path)

    # Call LLM 
    raw_evaluation = _call_llm(
        image_data=image_data,
        media_type=media_type,
        system_prompt=FIGURE_EVALUATION_SYSTEM_PROMPT,
        llm=llm,
    )

    # Parse results
    feedback = _parse_with_pydantic(raw_evaluation)

    return feedback.to_dict()


__all__ = [
    "figure_check",
    "FigureFeedback",
]
