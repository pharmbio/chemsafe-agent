from __future__ import annotations

CONVERSATION_SCRIPT = """
<script>
(function() {
    function findBus() {
        const el = document.getElementById("conversation-action-bus");
        if (!el) return null;
        if (el.matches && el.matches("textarea, input")) return el;
        return el.querySelector ? el.querySelector("textarea, input") : null;
    }

    function sendAction(payload) {
        const bus = findBus();
        if (!bus) return;
        bus.value = JSON.stringify(Object.assign({ ts: Date.now() }, payload || {}));
        bus.dispatchEvent(new Event("input", { bubbles: true }));
        bus.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function enforceLightTheme() {
        const root = document.documentElement;
        if (root) {
            root.style.colorScheme = "light";
            root.classList.remove("dark");
            root.classList.add("light");
        }
        if (document.body) {
            document.body.style.colorScheme = "light";
            document.body.classList.remove("dark");
            document.body.classList.add("light");
        }
        try {
            const url = new URL(window.location.href);
            if (url.searchParams.get("__theme") !== "light") {
                url.searchParams.set("__theme", "light");
                window.history.replaceState(window.history.state, "", url.toString());
            }
        } catch (error) {
            console.warn("Unable to pin light theme", error);
        }
    }

    function observeThemeLock() {
        const root = document.documentElement;
        if (!root || root.dataset.lightThemeLocked === "1") return;
        root.dataset.lightThemeLocked = "1";
        const syncTheme = () => window.requestAnimationFrame(enforceLightTheme);
        const observer = new MutationObserver(syncTheme);
        observer.observe(root, { attributes: true, attributeFilter: ["class"] });
        if (document.body) {
            observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
        }
        window.addEventListener("popstate", syncTheme);
    }

    async function triggerDownload(anchor) {
        const url = anchor.getAttribute("href");
        if (!url) return;
        anchor.dataset.downloading = "1";
        try {
            const response = await fetch(url, { credentials: "same-origin" });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const blob = await response.blob();
            const filename = anchor.getAttribute("data-file-name") || anchor.textContent.trim() || "download";
            const blobUrl = window.URL.createObjectURL(blob);
            const temp = document.createElement("a");
            temp.href = blobUrl;
            temp.download = filename;
            document.body.appendChild(temp);
            temp.click();
            window.setTimeout(() => {
                document.body.removeChild(temp);
                window.URL.revokeObjectURL(blobUrl);
            }, 0);
        } catch (error) {
            console.error("Download failed", error);
            window.open(url, "_blank", "noopener");
        } finally {
            delete anchor.dataset.downloading;
        }
    }

    function bindHandlers() {
        const root = document.getElementById("conversation-list-root");
        if (!root) return;

        root.querySelectorAll("summary").forEach((summary) => {
            if (summary.dataset.repBound === "1") return;
            summary.dataset.repBound = "1";
            summary.addEventListener("click", (event) => {
                if (event.target && event.target.closest("[data-delete-thread]")) return;
                const parent = summary.closest("details");
                if (!parent) return;
                const threadId = parent.getAttribute("data-thread-id");
                if (threadId) sendAction({ type: "activate", thread_id: threadId });
            });
        });

        root.querySelectorAll("[data-delete-thread]").forEach((button) => {
            if (button.dataset.repBound === "1") return;
            button.dataset.repBound = "1";
            button.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                const threadId = button.getAttribute("data-delete-thread");
                const confirmMessage = button.getAttribute("data-confirm-message");
                if (!threadId) return;
                if (confirmMessage && !window.confirm(confirmMessage)) return;
                sendAction({ type: "delete", thread_id: threadId });
            });
        });

        root.querySelectorAll("[data-download-link]").forEach((link) => {
            if (link.dataset.repDownloadBound === "1") return;
            link.dataset.repDownloadBound = "1";
            link.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (link.dataset.downloading === "1") return;
                triggerDownload(link);
            });
        });
    }

    function initPartnerSlider(slider) {
        if (!slider || slider.dataset.sliderInitialized === "1") return;
        const viewport = slider.querySelector(".partner-slider__viewport");
        const track = slider.querySelector(".partner-slider__track");
        const cards = Array.from(slider.querySelectorAll(".partner-logo-card"));
        const dots = slider.querySelector(".partner-slider__dots");
        if (!viewport || !track || !cards.length || !dots) return;
        const state = { index: 0, perSlide: 1, total: 1 };

        function applyTransform() {
            const viewportWidth = viewport.getBoundingClientRect().width || 1;
            track.style.transform = `translateX(-${state.index * viewportWidth}px)`;
        }

        function goTo(index) {
            state.index = Math.max(0, Math.min(index, state.total - 1));
            applyTransform();
            renderDots();
        }

        function renderDots() {
            dots.innerHTML = "";
            if (state.total <= 1) {
                dots.style.display = "none";
                return;
            }
            dots.style.display = "flex";
            for (let i = 0; i < state.total; i += 1) {
                const dot = document.createElement("button");
                dot.type = "button";
                dot.className = "partner-slider__dot" + (i === state.index ? " is-active" : "");
                dot.addEventListener("click", () => goTo(i));
                dots.appendChild(dot);
            }
        }

        function recalc() {
            const viewportWidth = viewport.getBoundingClientRect().width || 1;
            const sampleWidth = cards[0].getBoundingClientRect().width || 1;
            const styles = window.getComputedStyle(track);
            const gap = parseFloat(styles.columnGap || styles.gap || "16") || 16;
            const perSlide = Math.max(1, Math.floor((viewportWidth + gap) / (sampleWidth + gap)));
            state.perSlide = perSlide;
            state.total = Math.max(1, Math.ceil(cards.length / perSlide));
            state.index = Math.min(state.index, state.total - 1);
            renderDots();
            applyTransform();
        }

        const requestRecalc = () => window.requestAnimationFrame(recalc);
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(requestRecalc);
            ro.observe(viewport);
        } else {
            window.addEventListener("resize", requestRecalc);
        }
        requestRecalc();
        slider.dataset.sliderInitialized = "1";
    }

    function initPartnerSliders() {
        document.querySelectorAll("[data-partner-slider]").forEach((slider) => initPartnerSlider(slider));
    }

    function ensureReady() {
        enforceLightTheme();
        observeThemeLock();
        bindHandlers();
        initPartnerSliders();
    }

    ensureReady();
    const observer = new MutationObserver(() => {
        window.requestAnimationFrame(ensureReady);
    });
    observer.observe(document.body, { childList: true, subtree: true });
})();
</script>
"""
