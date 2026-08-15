from __future__ import annotations

import gradio as gr

from app.config import APP_TITLE
from app.run_controller import (
    FILE_LIST_REFRESH_INTERVAL_SECONDS,
    on_approve_plan,
    on_request_changes,
    on_send_message,
    on_stop_run,
)
from app.session import (
    on_app_load,
    on_clear_files,
    on_conversation_action,
    on_files_uploaded,
    on_login,
    on_logout,
    on_new_task,
    on_periodic_file_refresh,
    on_register,
)
from app.ui.assets import HEADER_LINKS_HTML, intro_markdown, logo_html, partner_logos_html

from app.ui.scripts import CONVERSATION_SCRIPT
from app.ui.theme import APP_CSS, CHEMSAFE_THEME


def build_demo() -> gr.Blocks:
    with gr.Blocks(
        title=APP_TITLE,
        theme=CHEMSAFE_THEME,
        css=APP_CSS,
        head=CONVERSATION_SCRIPT,
    ) as demo:
        state = gr.State()

        with gr.Row(elem_id="app-header"):
            logo_markup = logo_html()
            if logo_markup:
                with gr.Column(scale=0, min_width=96):
                    gr.HTML(logo_markup, elem_id="app-logo")
            with gr.Column(scale=1):
                gr.HTML(f"<div class='app-title-text'>{APP_TITLE}</div>", elem_id="app-title")
            with gr.Column(scale=0, min_width=260, elem_id="header-links-column"):
                gr.HTML(HEADER_LINKS_HTML, elem_id="header-links")

        partner_panel = partner_logos_html()
        if partner_panel:
            gr.HTML(partner_panel, elem_id="partner-logos-panel")

        with gr.Row(elem_id="layout-row"):
            with gr.Column(scale=1, min_width=280, elem_id="sidebar-column"):
                auth_status_md = gr.Markdown(
                    value="**Login to use the ChemSafeAgent**", elem_id="auth-status"
                )
                with gr.Tabs(elem_id="auth-tabs"):
                    with gr.Tab("Login"):
                        login_email = gr.Textbox(label="Email", placeholder="you@example.com")
                        login_password = gr.Textbox(label="Password", type="password")
                        login_btn = gr.Button("Log in", variant="primary")
                    with gr.Tab("Register"):
                        register_email = gr.Textbox(label="Email", placeholder="you@example.com")
                        register_password = gr.Textbox(label="Password", type="password")
                        register_confirm = gr.Textbox(label="Confirm Password", type="password")
                        register_btn = gr.Button("Create account")
                logout_btn = gr.Button("Log out", visible=False, elem_id="logout-button")

                conversation_list = gr.HTML(
                    value="", elem_id="conversation-list", min_height=10, container=False
                )
                conversation_action_bus = gr.Textbox(
                    value="", show_label=False, elem_id="conversation-action-bus"
                )
                file_refresh_timer = gr.Timer(
                    value=FILE_LIST_REFRESH_INTERVAL_SECONDS, active=True, render=False
                )
                # Starts disabled: `demo.load` enables it once auth resolves, and
                # a button that is clickable for that first moment invites a
                # click that can only produce "please sign in first".
                new_task_btn = gr.Button(
                    "New Task", interactive=False, elem_id="new-task-button"
                )
                file_upload = gr.File(label="Upload files", file_count="multiple", file_types=["file"], elem_id="file-upload-panel")
                clear_files_btn = gr.Button("Clear Files", elem_id="clear-files-button")

            with gr.Column(scale=4, elem_id="conversation-column"):
                gr.Markdown(intro_markdown(), elem_id="intro-text")
                chatbot = gr.Chatbot(
                    label="Conversation", height=560, type="messages", elem_id="chatbot-panel"
                )

                # The live plan. Sits directly under the transcript because
                # "which step are we on" is the question a long run raises most
                # often, and it used to be answerable only by expanding a
                # collapsed `plan_update` tool result.
                progress_panel = gr.HTML(
                    value="", visible=False, elem_id="progress-panel", container=False
                )

                # The plan-approval gate. Hidden until the graph actually pauses.
                approval_banner = gr.HTML(
                    value="", visible=False, elem_id="approval-banner", container=False
                )
                with gr.Row(elem_id="approval-actions-row"):
                    approve_btn = gr.Button(
                        "✓ Approve plan",
                        variant="primary",
                        visible=False,
                        elem_id="approve-button",
                    )
                    request_changes_btn = gr.Button(
                        "✎ Request changes",
                        variant="secondary",
                        visible=False,
                        elem_id="request-changes-button",
                    )

                user_input = gr.Textbox(label="Your message", lines=3, elem_id="user-input")
                with gr.Row(elem_id="input-actions-row"):
                    with gr.Column(scale=9):
                        send_btn = gr.Button("Send", variant="primary", elem_id="send-button")
                    with gr.Column(scale=1, min_width=120):
                        stop_btn = gr.Button(
                            "Stop",
                            variant="secondary",
                            interactive=False,
                            elem_id="stop-button",
                        )

        # One output shape for every handler that can change the workspace.
        workspace_outputs = [
            state,
            chatbot,
            user_input,
            conversation_list,
            approval_banner,
            approve_btn,
            request_changes_btn,
            send_btn,
            stop_btn,
            progress_panel,
        ]
        auth_outputs = workspace_outputs + [auth_status_md, logout_btn, login_btn, new_task_btn]

        demo.load(on_app_load, inputs=None, outputs=auth_outputs + [conversation_action_bus])

        conversation_action_bus.change(
            on_conversation_action,
            inputs=[conversation_action_bus, state],
            outputs=workspace_outputs + [conversation_action_bus],
        )

        file_refresh_timer.tick(
            on_periodic_file_refresh,
            inputs=[state],
            outputs=[state, conversation_list, progress_panel],
            trigger_mode="always_last",
        )

        login_btn.click(
            on_login,
            inputs=[login_email, login_password, state],
            outputs=auth_outputs,
        )
        register_btn.click(
            on_register,
            inputs=[register_email, register_password, register_confirm, state],
            outputs=auth_outputs,
        )
        logout_btn.click(on_logout, inputs=state, outputs=auth_outputs)

        new_task_btn.click(on_new_task, inputs=state, outputs=workspace_outputs)

        file_upload.upload(
            on_files_uploaded, inputs=[file_upload, state], outputs=[state, conversation_list]
        )
        clear_files_btn.click(on_clear_files, inputs=state, outputs=[state, conversation_list])

        send_btn.click(on_send_message, inputs=[user_input, state], outputs=workspace_outputs)
        user_input.submit(on_send_message, inputs=[user_input, state], outputs=workspace_outputs)

        approve_btn.click(on_approve_plan, inputs=state, outputs=workspace_outputs)
        request_changes_btn.click(on_request_changes, inputs=state, outputs=workspace_outputs)

        stop_btn.click(on_stop_run, inputs=state, outputs=workspace_outputs)

    return demo.queue(default_concurrency_limit=4, max_size=32)
