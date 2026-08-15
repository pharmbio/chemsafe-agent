from __future__ import annotations

from gradio.themes.utils import colors

import gradio as gr


PRIMARY_FERN = colors.Color(
    c50="#ebf6ff",
    c100="#d6ebfb",
    c200="#b4d8f1",
    c300="#8ec3e4",
    c400="#5fa8d4",
    c500="#2f88c1",
    c600="#025e8d",
    c700="#01486d",
    c800="#01334e",
    c900="#001f33",
    c950="#00111d",
    name="chemsafe_primary_blue",
)

SECONDARY_SAGE = colors.Color(
    c50="#f8f8f8",
    c100="#f1f1f1",
    c200="#e6e6e6",
    c300="#d5d5d5",
    c400="#bcbcbc",
    c500="#8d8d8d",
    c600="#666666",
    c700="#4a4a4a",
    c800="#2f2f2f",
    c900="#1f1f1f",
    c950="#121212",
    name="chemsafe_secondary_neutral",
)

CHEMSAFE_THEME = (
    gr.themes.Default(
        primary_hue=PRIMARY_FERN,
        secondary_hue=SECONDARY_SAGE,
        neutral_hue=colors.gray,
    ).set(
        color_accent="*primary_600",
        color_accent_soft="#ebf6ff",
        color_accent_soft_dark="*primary_700",
        button_primary_background_fill="*primary_600",
        button_primary_background_fill_hover="*primary_500",
        button_primary_text_color="#ffffff",
        button_primary_text_color_hover="#ffffff",
    )
)


APP_CSS = """
    :root {
        color-scheme: light;
        --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        --font-editorial: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
        --page-bg: #f8f8f8;
        --surface-bg: #ffffff;
        --surface-muted: #fbfbf8;
        --surface-tint: #f5f7f8;
        --field-bg: #ffffff;
        --text-main: #1f1f1f;
        --text-soft: #5f6368;
        --border-subtle: #d5d5d5;
        --border-strong: #bcbcbc;
        --link-color: #025e8d;
        --link-hover-color: #01486d;
        --focus-color: #fece3e;
        --header-link-color: #1f1f1f;
        --header-link-divider-color: #8d8d8d;
        --header-link-hover-color: #025e8d;
        --partner-card-width: 220px;
        --partner-card-gap: 1.15rem;
    }
    html,
    body,
    .gradio-container {
        color-scheme: light !important;
        font-family: var(--font-ui);
        background: var(--page-bg);
        color: var(--text-main);
    }
    .gradio-container *,
    .gradio-container *::before,
    .gradio-container *::after {
        box-sizing: border-box;
    }
    .gradio-container {
        max-width: none;
        width: 100vw;
        margin: 0 auto !important;
        padding: 1.5rem 1.25rem 2rem;
    }
    .gradio-container a {
        color: var(--link-color);
        text-decoration-thickness: 0.06em;
        text-underline-offset: 0.14em;
    }
    .gradio-container a:hover,
    .gradio-container a:focus {
        color: var(--link-hover-color);
    }
    .gradio-container button,
    .gradio-container input,
    .gradio-container textarea,
    .gradio-container label,
    .gradio-container .tabs,
    .gradio-container .tabitem {
        font-family: var(--font-ui) !important;
    }
    .gradio-container button:focus-visible,
    .gradio-container input:focus-visible,
    .gradio-container textarea:focus-visible,
    .gradio-container [role="tab"]:focus-visible,
    .conversation-card__delete:focus-visible,
    .partner-logo-card:focus-visible {
        outline: 3px solid var(--focus-color);
        outline-offset: 2px;
    }
    #app-header {
        align-items: center;
        gap: 1rem;
        margin-bottom: 0.8rem;
        padding: 0 0 0.9rem;
        border-bottom: 1px solid var(--border-strong);
    }
    #app-logo {
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0 !important;
    }
    #app-logo .app-logo-img {
        width: 84px;
        height: 84px;
        object-fit: contain;
        display: block;
    }
    #app-title {
        margin: 0 !important;
        padding: 0 !important;
        display: flex;
        align-items: center;
    }
    #app-title .app-title-text {
        font-family: var(--font-editorial);
        font-size: clamp(2.75rem, 4vw, 3.5rem);
        font-weight: 700;
        letter-spacing: -0.025em;
        line-height: 0.95;
        margin: 0;
        color: var(--text-main);
    }
    #header-links-column {
        margin-left: auto;
        padding: 0 !important;
        display: flex;
        justify-content: flex-end;
        align-items: center;
        overflow: visible !important;
    }
    #header-links {
        display: flex;
        gap: 0.75rem;
        align-items: center;
        font-weight: 600;
        font-size: 0.92rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--header-link-color);
        overflow: visible !important;
        white-space: nowrap;
    }
    #header-links .header-link {
        color: inherit;
        text-decoration: none;
        transition: color 0.2s ease;
        white-space: nowrap;
    }
    #header-links .header-link-divider {
        color: var(--header-link-divider-color);
        font-weight: 400;
        padding: 0 1.25rem;
        user-select: none;
    }
    #header-links .header-link:hover,
    #header-links .header-link:focus {
        color: var(--header-link-hover-color);
        text-decoration: underline;
    }
    #partner-logos-panel {
        width: 100%;
        margin: 0 auto 0.85rem;
        padding: 0.4rem 0 0.1rem;
        border-top: 1px solid var(--border-subtle);
        border-bottom: 1px solid var(--border-subtle);
        background: linear-gradient(180deg, #fafaf9 0%, #f8f8f8 100%);
    }
    #partner-logos-panel .partner-slider {
        width: 100%;
        margin: 0;
    }
    .partner-slider__viewport {
        overflow: hidden;
        width: 100%;
    }
    .partner-slider__track {
        display: flex;
        gap: var(--partner-card-gap);
        padding: 0.25rem;
        will-change: transform;
        transition: transform 0.4s ease;
    }
    .partner-logo-card {
        background: var(--surface-bg);
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        padding: 1rem 1.7rem;
        min-height: 108px;
        min-width: var(--partner-card-width);
        width: var(--partner-card-width);
        max-width: var(--partner-card-width);
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: none;
        transition: background 0.15s ease, border-color 0.15s ease;
        flex: 0 0 var(--partner-card-width);
    }
    .partner-logo-card:hover,
    .partner-logo-card:focus-visible {
        background: #fcfcf9;
        border-color: var(--link-color);
    }
    .partner-logo-card img {
        max-height: 70px;
        max-width: calc(var(--partner-card-width) - 20px);
        width: auto;
        height: auto;
        object-fit: contain;
        filter: saturate(1.05);
    }
    .partner-logo-card--xl {
        min-width: calc(var(--partner-card-width) + 60px);
        width: calc(var(--partner-card-width) + 60px);
        max-width: calc(var(--partner-card-width) + 60px);
    }
    .partner-logo-card--xl img {
        max-height: 90px;
        max-width: calc(var(--partner-card-width) + 20px);
    }
    .partner-slider__dots { display: flex; justify-content: center; gap: 0.45rem; margin-top: 0.5rem; }
    .partner-slider__dot { width: 9px; height: 9px; border-radius: 999px; background: #d5d5d5; border: 0; cursor: pointer; transition: all 0.2s ease; }
    .partner-slider__dot.is-active { background: var(--link-color); border: 0; }
    #intro-text {
        margin: 0 0 0.18rem 0 !important;
        padding: 0;
        width: 100%;
    }
    #intro-text,
    #intro-text *,
    #chatbot-panel,
    #chatbot-panel .prose,
    #chatbot-panel .prose p,
    #chatbot-panel .prose li,
    #chatbot-panel .message,
    #chatbot-panel [data-testid*="assistant"],
    #chatbot-panel [data-testid*="assistant"] * {
        font-family: var(--font-editorial) !important;
    }
    #intro-text img {
        width: 100%;
        max-width: 100%;
        max-height: 330px;
        height: auto;
        display: block;
        margin: 0 auto;
        border-radius: 0;
        border: 1px solid var(--border-subtle);
        box-shadow: none;
        object-fit: contain;
        background: #fff;
    }
    #layout-row {
        width: 100%;
        gap: 1.5rem;
        align-items: flex-start;
    }
    #layout-row > div {
        min-width: 0;
    }
    #conversation-column {
        display: flex;
        flex-direction: column;
        gap: 0.55rem;
        padding: 1rem 1rem 1.1rem;
        background: var(--page-bg);
        border: 1px solid transparent;
        flex: 1 1 0 !important;
        width: auto !important;
        min-width: 0 !important;
    }
    #sidebar-column {
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
        position: sticky;
        top: 1rem;
        align-self: flex-start;
        min-width: 312px;
        width: 312px !important;
        flex: 0 0 312px !important;
        max-width: 312px;
    }
    #sidebar-column > div {
        background: var(--page-bg);
        border: 1px solid transparent;
        border-radius: 0;
        box-shadow: none;
        padding: 0.2rem;
    }
    #conversation-column > div {
        background: transparent;
        border: 0;
        border-radius: 0;
        box-shadow: none;
    }
    #conversation-list {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
        padding: 0 !important;
    }
    #conversation-list > div {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
    }
    #conversation-action-bus { display: none !important; }
    #input-actions-row {
        margin-top: 0.15rem;
        gap: 0.65rem;
        padding-top: 0.35rem;
        border-top: 1px solid var(--border-subtle);
        align-items: stretch;
    }
    #send-button, #stop-button { width: 100%; }
    #stop-button { min-width: 120px; }
    #auth-status {
        padding: 0.65rem 0.75rem !important;
        background: var(--surface-tint);
    }
    #auth-status p {
        margin: 0 !important;
        font-family: var(--font-ui) !important;
        font-size: 0.88rem !important;
        line-height: 1.45 !important;
    }
    #auth-tabs {
        padding: 0.2rem !important;
    }
    #auth-tabs > div,
    #auth-tabs > div > div,
    #auth-tabs .tabitem,
    #auth-tabs .tabitem > div {
        background: transparent !important;
        box-shadow: none !important;
    }
    #auth-tabs .tabitem {
        border: 0 !important;
        padding: 0.9rem 0.35rem 0.2rem !important;
    }
    #auth-tabs .tabitem > div {
        border: 0 !important;
        padding: 0 !important;
    }
    #sidebar-column .tabs,
    #sidebar-column .tab-nav,
    #sidebar-column .tabitem {
        border-radius: 0 !important;
    }
    #sidebar-column .tab-nav {
        border-bottom: 1px solid var(--border-subtle) !important;
        padding: 0 0.25rem !important;
    }
    #sidebar-column [role="tab"] {
        border-radius: 0 !important;
        border-bottom: 2px solid transparent !important;
        padding: 0.7rem 0.2rem 0.65rem !important;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        font-size: 0.74rem !important;
        font-weight: 700 !important;
    }
    #sidebar-column [role="tab"][aria-selected="true"] {
        color: var(--link-color) !important;
        border-bottom-color: var(--link-color) !important;
    }
    #sidebar-column input,
    #sidebar-column textarea,
    #conversation-column textarea {
        border-radius: 0 !important;
        border: 1px solid var(--border-strong) !important;
        background: var(--field-bg) !important;
        color: var(--text-main) !important;
    }
    #sidebar-column input:focus,
    #sidebar-column textarea:focus,
    #conversation-column textarea:focus {
        background: var(--field-bg) !important;
        border-color: var(--link-color) !important;
        box-shadow: none !important;
    }
    #sidebar-column input:-webkit-autofill,
    #sidebar-column input:-webkit-autofill:hover,
    #sidebar-column input:-webkit-autofill:focus,
    #conversation-column textarea:-webkit-autofill,
    #conversation-column textarea:-webkit-autofill:hover,
    #conversation-column textarea:-webkit-autofill:focus {
        -webkit-text-fill-color: var(--text-main) !important;
        -webkit-box-shadow: 0 0 0 1000px var(--field-bg) inset !important;
        transition: background-color 9999s ease-out 0s;
    }
    #sidebar-column label,
    #conversation-column label {
        font-size: 0.84rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-soft) !important;
    }
    #sidebar-column button,
    #conversation-column button {
        border-radius: 0 !important;
        box-shadow: none !important;
        font-weight: 700 !important;
        letter-spacing: 0.02em;
    }
    #logout-button,
    #new-task-button,
    #clear-files-button {
        min-height: 42px;
    }
    #new-task-button button:disabled {
        background: #efefef !important;
        color: #8d8d8d !important;
        border-color: var(--border-subtle) !important;
        opacity: 1 !important;
    }
    #stop-button button,
    #sidebar-column button.secondary {
        border: 1px solid var(--border-strong) !important;
    }
    #file-upload-panel {
        background: transparent !important;
        border: 0 !important;
        padding: 0 !important;
        box-shadow: none !important;
        width: 100% !important;
    }
    #file-upload-panel > div,
    #file-upload-panel > div > div {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
        width: 100% !important;
    }
    #file-upload-panel .wrap {
        border: 1px dashed var(--border-strong) !important;
        background: linear-gradient(180deg, #ffffff 0%, #fbfbf8 100%) !important;
        min-height: 150px;
        padding: 0.85rem !important;
        width: 100% !important;
        max-width: 100% !important;
    }
    #file-upload-panel .or,
    #file-upload-panel .hint {
        color: var(--text-soft) !important;
    }
    #file-upload-panel .label-wrap,
    #file-upload-panel .label-wrap label {
        background: transparent !important;
        box-shadow: none !important;
    }
    #file-upload-panel .label-wrap {
        width: 100% !important;
        margin-bottom: 0 !important;
    }
    #file-upload-panel .label-wrap label {
        display: block !important;
        width: 100% !important;
    }
    #file-upload-panel .center,
    #file-upload-panel .upload-container,
    #file-upload-panel [data-testid="file-upload-dropzone"] {
        width: 100% !important;
        max-width: 100% !important;
    }
    #intro-text {
        padding-bottom: 0.3rem;
        border-bottom: 1px solid var(--border-subtle);
    }
    #chatbot-panel {
        font-size: 1.03rem;
        line-height: 1.68;
        border-radius: 0 !important;
        background: var(--surface-bg) !important;
        border: 1px solid var(--border-subtle) !important;
    }
    #chatbot-panel .prose,
    #chatbot-panel .prose p,
    #chatbot-panel .bot-message *,
    #chatbot-panel .message.bot *,
    #chatbot-panel [data-testid*="assistant"],
    #chatbot-panel [data-testid*="assistant"] * {
        font-size: inherit !important;
        line-height: inherit !important;
        color: var(--text-main) !important;
    }
    #chatbot-panel .user-message *,
    #chatbot-panel .message.user *,
    #chatbot-panel [data-testid*="user"],
    #chatbot-panel [data-testid*="user"] * {
        font-size: 0.98rem !important;
        line-height: 1.6 !important;
        font-family: var(--font-ui) !important;
    }
    #chatbot-panel [data-testid="chatbot-avatar"] {
        border-radius: 0 !important;
    }
    #chatbot-panel .bubble-wrap,
    #chatbot-panel .message-wrap {
        padding-left: 0.15rem !important;
        padding-right: 0.15rem !important;
    }
    #chatbot-panel .message,
    #chatbot-panel .message-row {
        border-radius: 0 !important;
    }
    #chatbot-panel [data-testid*="assistant"] {
        background: #fff !important;
    }
    #chatbot-panel .bubble.user-row {
        background: #f4f7f9 !important;
        border: 1px solid #d8e2e7 !important;
        box-shadow: none !important;
        padding: 0.65rem 0.9rem !important;
    }
    #chatbot-panel .bubble.user-row .user,
    #chatbot-panel .bubble.user-row .message.user,
    #chatbot-panel .bubble.user-row .message.user > div {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
        padding: 0 !important;
    }
    #chatbot-panel code,
    #chatbot-panel pre,
    .tool-code-block pre,
    details.tool-block pre {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace !important;
    }
    .agent-message-inline {
        margin: 0.9rem 0;
        white-space: pre-wrap;
        word-break: break-word;
        color: var(--text-main);
    }
    details.tool-block {
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        padding: 0.75rem 0.95rem;
        background: var(--surface-muted);
        margin: 0.9rem 0;
    }
    details.tool-block summary {
        font-family: var(--font-ui);
        font-weight: 700;
        color: var(--text-main);
        cursor: pointer;
        letter-spacing: 0.02em;
    }
    details.tool-block pre {
        margin: 0.75rem 0 0 0;
        font-size: 0.92rem;
        background: #f4f7f9;
        padding: 0.85rem 1rem;
        border-radius: 0;
        overflow-x: auto;
        white-space: pre-wrap;
        border: 1px solid var(--border-subtle);
    }
    .tool-code-block {
        background: #f4f7f9;
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        padding: 0.95rem 1rem;
        margin-top: 0.75rem;
        overflow-x: auto;
    }
    .tool-code-label {
        font-family: var(--font-ui);
        font-size: 0.72rem;
        letter-spacing: 0.1em;
        font-weight: 700;
        color: var(--text-soft);
        margin-bottom: 0.45rem;
    }
    .tool-code-block pre {
        margin: 0;
        font-size: 0.92rem;
        line-height: 1.55;
        color: var(--text-main);
        background: transparent;
        white-space: pre;
    }
    .agent-error-card {
        border: 1px solid #d98b8b;
        border-left: 4px solid #c0392b;
        border-radius: 0;
        background: #fdf3f2;
        padding: 0.9rem 1.05rem;
        margin: 0.9rem 0;
    }
    .agent-error-card__title {
        font-family: var(--font-ui);
        font-weight: 700;
        letter-spacing: 0.02em;
        color: #a5281b;
        margin-bottom: 0.35rem;
    }
    .agent-error-card__title::before {
        content: "\\26A0";
        margin-right: 0.5rem;
    }
    .agent-error-card__message {
        font-family: var(--font-ui);
        font-size: 0.95rem;
        line-height: 1.55;
        color: var(--text-main);
    }
    .agent-error-card__detail { margin-top: 0.6rem; }
    .agent-error-card__detail summary {
        font-family: var(--font-ui);
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
        cursor: pointer;
    }
    .agent-error-card__detail pre {
        margin: 0.55rem 0 0;
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        font-size: 0.85rem;
        line-height: 1.5;
        background: #ffffff;
        border: 1px solid #e6c9c6;
        border-radius: 0;
        padding: 0.7rem 0.85rem;
        overflow-x: auto;
        white-space: pre-wrap;
        color: #6b2b23;
    }
    /* ---- Three visual tiers -------------------------------------------------
       The run produces working narration, a plan to review, and a final report.
       Rendering all three identically made the answer as hard to spot as a
       stray tool call, so each gets its own weight. */

    /* Working narration: quieter than the deliverable, still readable. */
    .agent-message-section--activity {
        font-size: 0.94rem;
        color: var(--text-main);
    }

    /* The plan under review. */
    .agent-block-content--plan {
        border-left: 3px solid var(--link-color);
        padding-left: 0.9rem;
    }
    .agent-message-section--plan h1,
    .agent-message-section--plan h2,
    .agent-message-section--plan h3 { font-family: var(--font-ui); }
    .agent-message-section--plan strong { color: var(--link-hover-color); }

    /* The deliverable. Reads as a document, not as another chat bubble. */
    .agent-block-content--report {
        background: var(--surface-bg);
        border: 1px solid var(--border-subtle);
        border-top: 3px solid var(--link-color);
        padding: 1.15rem 1.35rem 1.05rem;
        margin: 0.3rem 0;
    }
    .agent-message-section--report {
        font-family: var(--font-ui);
        font-size: 0.97rem;
        line-height: 1.62;
    }
    .agent-message-section--report h1 {
        font-family: var(--font-editorial);
        font-size: 1.42rem;
        line-height: 1.2;
        margin: 0 0 0.75rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid var(--border-subtle);
        color: var(--text-main);
    }
    .agent-message-section--report h2 {
        font-family: var(--font-ui);
        font-size: 0.76rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--text-soft);
        margin: 1.15rem 0 0.4rem;
    }
    .agent-message-section--report h2:first-of-type { margin-top: 0.2rem; }
    .agent-message-section--report ul { margin: 0.3rem 0 0.3rem 1.1rem; }
    .agent-message-section--report li { margin-bottom: 0.3rem; }
    .agent-message-section--report code {
        background: var(--surface-tint);
        padding: 0.08em 0.35em;
        font-size: 0.9em;
    }
    .agent-message-section--report table {
        border-collapse: collapse;
        width: 100%;
        margin: 0.5rem 0;
        font-size: 0.9rem;
    }
    .agent-message-section--report th,
    .agent-message-section--report td {
        border: 1px solid var(--border-subtle);
        padding: 0.4rem 0.55rem;
        text-align: left;
    }
    .agent-message-section--report th { background: var(--surface-tint); font-weight: 700; }

    /* ---- Tool activity -------------------------------------------------------
       One line per action, saying what was done and how it went. The previous
       build showed a "Tools Calling" box and a "Tools Result" box per call, both
       labelled by tool name, so the reader had to expand each one to find out
       whether anything had gone wrong. */
    .tool-entry {
        border: 1px solid var(--border-subtle);
        border-left: 3px solid var(--border-strong);
        background: var(--surface-muted);
        margin: 0.3rem 0;
    }
    .tool-entry > summary {
        display: flex;
        align-items: center;
        gap: 0.55rem;
        padding: 0.42rem 0.7rem;
        cursor: pointer;
        font-family: var(--font-ui);
        font-size: 0.87rem;
        color: var(--text-main);
        list-style: none;
    }
    .tool-entry > summary::-webkit-details-marker { display: none; }
    .tool-entry__icon { flex: 0 0 auto; opacity: 0.75; font-size: 0.9rem; }
    .tool-entry__label {
        flex: 1 1 auto;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .tool-entry__note {
        flex: 0 0 auto;
        font-size: 0.78rem;
        color: var(--text-soft);
        white-space: nowrap;
    }
    .tool-entry__mark { flex: 0 0 auto; font-size: 0.82rem; font-weight: 700; width: 0.9rem; text-align: center; }
    .tool-entry__mark--ok { color: #2f8f4e; }
    .tool-entry__mark--error { color: #c0392b; }
    .tool-entry__mark--running {
        border-radius: 50%;
        width: 0.5rem;
        height: 0.5rem;
        background: var(--link-color);
        animation: chemsafe-pulse 1.4s ease-in-out infinite;
    }
    .tool-entry--error { border-left-color: #c0392b; background: #fdf3f2; }
    .tool-entry--ok { border-left-color: #cfd8d2; }
    .tool-entry__body { padding: 0 0.7rem 0.6rem; }
    .tool-entry__body:empty { padding: 0; }
    .tool-recovery {
        font-family: var(--font-ui);
        font-size: 0.85rem;
        line-height: 1.5;
        color: var(--text-soft);
        margin: 0.5rem 0 0;
    }

    /* ---- Live plan panel ----------------------------------------------------- */
    #progress-panel { margin: 0.55rem 0 0; }
    .plan-panel {
        border: 1px solid var(--border-subtle);
        border-left: 3px solid var(--link-color);
        background: var(--surface-bg);
    }
    .plan-panel__summary {
        padding: 0.7rem 0.9rem;
        cursor: pointer;
        list-style: none;
    }
    .plan-panel__summary::-webkit-details-marker { display: none; }
    .plan-panel__head {
        display: flex;
        align-items: baseline;
        gap: 0.6rem;
    }
    .plan-panel__title {
        font-family: var(--font-ui);
        font-size: 0.74rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--text-soft);
    }
    .plan-panel__count {
        margin-left: auto;
        font-family: var(--font-ui);
        font-size: 0.8rem;
        font-weight: 700;
        color: var(--link-hover-color);
    }
    .plan-panel__goal {
        font-family: var(--font-ui);
        font-size: 0.92rem;
        color: var(--text-main);
        margin-top: 0.25rem;
    }
    .plan-panel__caption {
        font-family: var(--font-ui);
        font-size: 0.83rem;
        color: var(--text-soft);
        margin-top: 0.15rem;
    }
    .plan-panel__bar {
        margin-top: 0.5rem;
        height: 4px;
        background: var(--surface-tint);
        border: 1px solid var(--border-subtle);
        overflow: hidden;
    }
    .plan-panel__bar > span {
        display: block;
        height: 100%;
        background: var(--link-color);
        transition: width 0.3s ease;
    }
    .plan-panel__conditions {
        margin-top: 0.5rem;
        font-family: var(--font-ui);
        font-size: 0.83rem;
        color: var(--text-soft);
    }
    .plan-panel__conditions-title {
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        font-size: 0.72rem;
    }
    .plan-panel__conditions ul { margin: 0.2rem 0 0 1rem; }
    .plan-panel__steps {
        list-style: none;
        margin: 0;
        padding: 0.2rem 0.9rem 0.8rem;
        border-top: 1px solid var(--border-subtle);
    }
    .plan-step {
        display: flex;
        gap: 0.55rem;
        padding: 0.28rem 0;
        font-family: var(--font-ui);
        font-size: 0.88rem;
        line-height: 1.45;
    }
    .plan-step__mark {
        flex: 0 0 1rem;
        text-align: center;
        font-weight: 700;
        color: var(--border-strong);
    }
    .plan-step__body { display: flex; flex-direction: column; }
    .plan-step__note { font-size: 0.8rem; color: var(--text-soft); }
    .plan-step--done .plan-step__mark { color: #2f8f4e; }
    .plan-step--done .plan-step__title { color: var(--text-soft); }
    .plan-step--active .plan-step__mark { color: var(--link-color); }
    .plan-step--active .plan-step__title { font-weight: 700; color: var(--text-main); }
    .plan-step--blocked .plan-step__mark { color: #c0392b; }
    .plan-step--blocked .plan-step__title { color: #a5281b; }
    .plan-step--skipped .plan-step__title { color: var(--text-soft); text-decoration: line-through; }

    /* ---- Artifact thumbnails ------------------------------------------------- */
    .conversation-card__thumb { margin: 0.3rem 0 0.45rem; }
    .conversation-card__thumb img {
        max-width: 100%;
        height: auto;
        display: block;
        border: 1px solid var(--border-subtle);
        background: #ffffff;
    }

    /* Neutral counterpart to the error card: a stopped run is not a failure. */
    .agent-notice-card {
        border: 1px solid var(--border-subtle);
        border-left: 4px solid var(--text-soft);
        background: var(--surface-tint);
        padding: 0.9rem 1.05rem;
        margin: 0.9rem 0;
    }
    .agent-notice-card__title {
        font-family: var(--font-ui);
        font-weight: 700;
        letter-spacing: 0.02em;
        color: var(--text-main);
        margin-bottom: 0.35rem;
    }
    .agent-notice-card__message {
        font-family: var(--font-ui);
        font-size: 0.95rem;
        line-height: 1.55;
        color: var(--text-soft);
    }
    /* The plan-approval gate. Deliberately the loudest thing on the page while
       it is up: the run is stopped until the user acts, and the previous build
       gave no indication of that at all. */
    #approval-banner { margin: 0.75rem 0 0; }
    .approval-panel {
        border: 1px solid var(--link-color);
        border-left: 4px solid var(--link-color);
        background: var(--color-accent-soft, #ebf6ff);
        padding: 0.95rem 1.1rem;
    }
    .approval-panel__title {
        font-family: var(--font-ui);
        font-weight: 700;
        font-size: 1rem;
        letter-spacing: 0.02em;
        color: var(--link-hover-color);
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .approval-panel__icon { font-size: 1.05rem; }
    .approval-panel__message {
        font-family: var(--font-ui);
        font-size: 0.95rem;
        line-height: 1.55;
        color: var(--text-main);
        margin-top: 0.4rem;
    }
    .approval-panel__hint {
        font-family: var(--font-ui);
        font-size: 0.85rem;
        line-height: 1.5;
        color: var(--text-soft);
        margin-top: 0.45rem;
    }
    #approval-actions-row {
        gap: 0.6rem;
        margin: 0.6rem 0 0;
    }
    /* Status dot on a conversation the user is not currently viewing. */
    .conversation-card__badge {
        margin-left: auto;
        margin-right: 0.35rem;
        font-size: 0.7rem;
        line-height: 1;
        flex: 0 0 auto;
    }
    .conversation-card__badge--running {
        color: var(--link-color);
        animation: chemsafe-pulse 1.4s ease-in-out infinite;
    }
    .conversation-card__badge--updated { color: #2f8f4e; }
    @keyframes chemsafe-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.25; }
    }
    @media (prefers-reduced-motion: reduce) {
        .conversation-card__badge--running { animation: none; }
    }
    #user-input {
        border: 0 !important;
        background: transparent !important;
        padding: 0 !important;
        box-shadow: none !important;
    }
    #user-input > div,
    #user-input > div > div {
        border: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
        padding: 0 !important;
    }
    #user-input textarea {
        min-height: 112px !important;
        line-height: 1.6 !important;
        padding: 0.8rem 0.9rem !important;
        border: 1px solid var(--border-subtle) !important;
    }
    #send-button button {
        min-height: 46px;
    }
    #stop-button button {
        min-height: 46px;
        background: #fff !important;
    }
    #conversation-list {
        margin-top: 0.5rem;
        font-family: var(--font-ui);
        width: 100%;
        display: block;
    }
    #conversation-list,
    #conversation-list > div,
    #conversation-list-root {
        width: 100%;
        box-sizing: border-box;
    }
    #conversation-list-root {
        border: 1px solid var(--border-subtle);
        border-radius: 0;
        background: var(--surface-bg);
        overflow: hidden;
        box-shadow: none;
    }
    .conversation-list__header {
        font-weight: 700;
        padding: 0.8rem 0.95rem;
        border-bottom: 1px solid var(--border-subtle);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.74rem;
        color: var(--text-soft);
        background: var(--surface-muted);
    }
    details.conversation-card { border-bottom: 1px solid #ececec; }
    details.conversation-card:last-child { border-bottom: none; }
    details.conversation-card summary {
        list-style: none;
        padding: 0.82rem 0.95rem;
        cursor: pointer;
        background: transparent;
        transition: background 0.2s ease, color 0.2s ease;
    }
    details.conversation-card summary::-webkit-details-marker { display: none; }
    details.conversation-card.is-active summary {
        background: #ebf6ff;
        color: var(--text-main);
    }
    .conversation-card__title-row { display: flex; align-items: flex-start; gap: 0.55rem; }
    .conversation-card__title { font-size: 0.9rem; font-weight: 600; color: inherit; flex: 1; line-height: 1.28; }
    .conversation-card__chevron { width: 12px; height: 12px; border-right: 2px solid currentColor; border-bottom: 2px solid currentColor; transform: rotate(45deg); transition: transform 0.2s ease; }
    .conversation-card__chevron { margin-top: 0.32rem; flex: 0 0 12px; }
    details.conversation-card[open] .conversation-card__chevron { transform: rotate(-135deg); }
    .conversation-card__delete {
        border: 1px solid var(--border-strong);
        border-radius: 0;
        padding: 0;
        font-size: 0.8rem;
        background: var(--surface-bg);
        cursor: pointer;
        color: var(--text-soft);
        transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease;
        margin-left: auto;
        width: 2rem;
        min-width: 2rem;
        height: 2rem;
        display: inline-flex;
        align-items: center;
        justify-content: center;
    }
    .conversation-card__delete:hover {
        background: var(--surface-muted);
        color: var(--text-main);
        border-color: var(--link-color);
    }
    .conversation-card__body {
        background: var(--surface-muted);
        padding: 0.55rem 0.95rem 0.9rem;
        border-top: 1px solid #ececec;
    }
    .conversation-card__files-container { max-height: 180px; overflow-y: auto; padding-right: 0.25rem; }
    .conversation-card__files { list-style: none; margin: 0; padding: 0; }
    .conversation-card__file-item { font-size: 0.82rem; color: var(--text-main); }
    .conversation-card__file-name { font-weight: 500; }
    .conversation-card__file-link { font-weight: 600; color: var(--link-color); text-decoration: none; }
    .conversation-card__file-link:hover, .conversation-card__file-link:focus { text-decoration: underline; }
    .conversation-card__file-more, .conversation-card__empty { font-size: 0.82rem; color: var(--text-soft); margin: 0; }
    footer {
        border-top: 1px solid var(--border-subtle);
        margin-top: 1rem !important;
        padding-top: 0.85rem !important;
        color: var(--text-soft) !important;
    }
    @media (max-width: 900px) {
        .gradio-container {
            width: 100vw;
            padding-top: 1rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }
        #app-header {
            gap: 0.75rem;
        }
        #header-links {
            font-size: 0.8rem;
            letter-spacing: 0.06em;
        }
        #layout-row {
            gap: 1rem;
        }
        #sidebar-column {
            position: static;
        }
        #conversation-column {
            padding: 0.8rem;
        }
        #chatbot-panel {
            font-size: 1rem;
            line-height: 1.62;
        }
    }
    """
