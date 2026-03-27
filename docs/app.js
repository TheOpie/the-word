/* TheWord — Event Rendering + Filtering */

(function () {
    "use strict";

    // SVG icons (inline to avoid dependencies)
    const ICON_CLOCK = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
    const ICON_PIN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>';
    const ICON_LINK = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';

    // Placeholder image mapping by primary tag
    const PLACEHOLDER_MAP = {
        Music: "img/placeholders/music.svg",
        "Local Bands": "img/placeholders/music.svg",
        "National Act": "img/placeholders/music.svg",
        "DJ/Electronic": "img/placeholders/music.svg",
        "Latin/World": "img/placeholders/music.svg",
        Comedy: "img/placeholders/comedy.svg",
        Sports: "img/placeholders/sports.svg",
        Theater: "img/placeholders/theater.svg",
        Art: "img/placeholders/art.svg",
        Exhibition: "img/placeholders/art.svg",
    };
    const DEFAULT_PLACEHOLDER = "img/placeholders/general.svg";

    let allEvents = [];
    let activeDateFilter = "all";
    let activeTagFilters = new Set();

    // --- Init ---
    document.addEventListener("DOMContentLoaded", init);

    async function init() {
        try {
            const resp = await fetch("events.json");
            if (!resp.ok) throw new Error("Failed to load events.json");
            allEvents = await resp.json();
        } catch (e) {
            console.error("Error loading events:", e);
            allEvents = [];
        }

        buildTagFilters();
        bindDateFilters();
        render();
        updateLastUpdated();
    }

    // --- Tag filter buttons ---
    function buildTagFilters() {
        const tagSet = new Set();
        allEvents.forEach(function (e) {
            (e.tags || []).forEach(function (t) { tagSet.add(t); });
        });

        var container = document.getElementById("tag-filters");
        var sorted = Array.from(tagSet).sort();
        sorted.forEach(function (tag) {
            var btn = document.createElement("button");
            btn.className = "filter-pill tag-pill";
            btn.dataset.tag = tag;
            btn.textContent = tag.toUpperCase();
            btn.addEventListener("click", function () {
                if (activeTagFilters.has(tag)) {
                    activeTagFilters.delete(tag);
                    btn.classList.remove("active");
                } else {
                    activeTagFilters.add(tag);
                    btn.classList.add("active");
                }
                render();
            });
            container.appendChild(btn);
        });
    }

    // --- Date filter buttons ---
    function bindDateFilters() {
        document.querySelectorAll(".date-pill").forEach(function (btn) {
            btn.addEventListener("click", function () {
                document.querySelectorAll(".date-pill").forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                activeDateFilter = btn.dataset.date;
                render();
            });
        });
    }

    // --- Filtering logic ---
    function filterEvents() {
        var now = new Date();
        var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        var tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);

        return allEvents.filter(function (event) {
            // Date filter
            if (activeDateFilter !== "all") {
                var eventDate = new Date(event.dateTime);
                var eventDay = new Date(eventDate.getFullYear(), eventDate.getMonth(), eventDate.getDate());

                if (activeDateFilter === "today") {
                    if (eventDay.getTime() !== today.getTime()) return false;
                } else if (activeDateFilter === "tomorrow") {
                    if (eventDay.getTime() !== tomorrow.getTime()) return false;
                } else if (activeDateFilter === "weekend") {
                    var day = eventDay.getDay();
                    if (day !== 0 && day !== 6) return false; // Sat=6, Sun=0
                }
            }

            // Tag filter (union — match ANY selected tag)
            if (activeTagFilters.size > 0) {
                var eventTags = new Set(event.tags || []);
                var match = false;
                activeTagFilters.forEach(function (t) {
                    if (eventTags.has(t)) match = true;
                });
                if (!match) return false;
            }

            return true;
        });
    }

    // --- Render ---
    function render() {
        var filtered = filterEvents();
        var grid = document.getElementById("events-grid");
        var noEvents = document.getElementById("no-events");
        var countEl = document.getElementById("event-count");

        grid.innerHTML = "";

        if (filtered.length === 0) {
            grid.style.display = "none";
            noEvents.style.display = "block";
            countEl.textContent = "";
            return;
        }

        grid.style.display = "grid";
        noEvents.style.display = "none";
        countEl.textContent = filtered.length + " EVENT" + (filtered.length !== 1 ? "S" : "");

        filtered.forEach(function (event) {
            grid.appendChild(createCard(event));
        });
    }

    // --- Card creation ---
    function createCard(event) {
        var card = document.createElement("div");
        card.className = "event-card";

        var dt = new Date(event.dateTime);
        var dateStr = dt.toLocaleDateString("en-US", {
            weekday: "short", month: "short", day: "numeric",
            timeZone: "America/Chicago",
        });
        var timeStr = dt.toLocaleTimeString("en-US", {
            hour: "numeric", minute: "2-digit",
            timeZone: "America/Chicago",
        });

        // Placeholder image
        var imgSrc = event.imageUrl || getPlaceholder(event.tags);

        var html = '<div class="card-image">' +
            '<img src="' + escHtml(imgSrc) + '" alt="' + escHtml(event.name) + '" onerror="this.src=\'' + DEFAULT_PLACEHOLDER + '\'">' +
            '<div class="card-image-overlay"></div>' +
            '<div class="card-date-badge">' +
            '<span class="date-badge">' + escHtml(dateStr) + '</span>';

        if (event.dateRange) {
            html += '<span class="date-range-badge">' + escHtml(event.dateRange) + '</span>';
        }

        html += '</div></div>';

        html += '<div class="card-body">';
        html += '<h3 class="card-title">' + escHtml(event.name) + '</h3>';

        // Tags
        if (event.tags && event.tags.length) {
            html += '<div class="card-tags">';
            event.tags.forEach(function (tag) {
                html += '<span class="tag-pill">' + escHtml(tag) + '</span>';
            });
            html += '</div>';
        }

        // Description
        if (event.description) {
            html += '<p class="card-description">' + escHtml(event.description) + '</p>';
        }

        // Time
        if (timeStr && timeStr !== "12:00 AM") {
            html += '<div class="card-meta">' + ICON_CLOCK + '<span>' + escHtml(timeStr) + '</span></div>';
        }

        // Venue
        html += '<div class="card-meta">' + ICON_PIN + '<span>' + escHtml(event.venue || "TBA") + '</span></div>';

        // Link
        if (event.sourceUrl) {
            html += '<a class="card-link" href="' + escHtml(event.sourceUrl) + '" target="_blank" rel="noopener noreferrer">VIEW EVENT ' + ICON_LINK + '</a>';
        }

        html += '</div>';

        card.innerHTML = html;
        return card;
    }

    function getPlaceholder(tags) {
        if (!tags || !tags.length) return DEFAULT_PLACEHOLDER;
        for (var i = 0; i < tags.length; i++) {
            if (PLACEHOLDER_MAP[tags[i]]) return PLACEHOLDER_MAP[tags[i]];
        }
        return DEFAULT_PLACEHOLDER;
    }

    function escHtml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function updateLastUpdated() {
        var el = document.getElementById("last-updated");
        if (allEvents.length > 0) {
            el.textContent = "LAST UPDATED: " + new Date().toLocaleDateString("en-US", {
                month: "long", day: "numeric", year: "numeric",
                timeZone: "America/Chicago",
            });
        }
    }
})();
