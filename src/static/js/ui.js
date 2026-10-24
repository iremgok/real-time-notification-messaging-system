// DOM rendering helpers. All user content is inserted with textContent (no XSS).
const $ = (id) => document.getElementById(id);

function showAuth() {
    $("auth-container").classList.remove("hidden");
    $("app-container").classList.add("hidden");
}

function showApp() {
    $("auth-container").classList.add("hidden");
    $("app-container").classList.remove("hidden");
}

function showAuthMessage(text, isError = true) {
    const el = $("auth-error");
    el.className = `mt-4 text-sm text-center ${isError ? "text-red-500" : "text-green-600"}`;
    el.textContent = text;
}

function switchAuthTab(tab) {
    const active = "w-1/2 pb-2 text-center font-semibold border-b-2 border-indigo-600 text-indigo-600";
    const inactive = "w-1/2 pb-2 text-center font-semibold text-gray-500 hover:text-indigo-600";
    $("tab-login").className = tab === "login" ? active : inactive;
    $("tab-register").className = tab === "register" ? active : inactive;
    $("login-form").classList.toggle("hidden", tab !== "login");
    $("register-form").classList.toggle("hidden", tab !== "register");
    $("auth-error").classList.add("hidden");
}

function updateConnectionBadge(online) {
    const badge = $("ws-status-badge");
    badge.textContent = online ? "Connected" : "Offline";
    badge.className = `${online ? "bg-green-500" : "bg-red-500"} text-white text-xs px-2 py-0.5 rounded-full font-medium`;
}

function showToast(title, body, level = "info") {
    const colors = { info: "bg-indigo-600", warning: "bg-yellow-500", critical: "bg-red-600" };
    const toast = document.createElement("div");
    toast.className = `${colors[level] || colors.info} text-white rounded-md shadow-lg p-3`;
    const titleEl = document.createElement("p");
    titleEl.className = "font-semibold text-sm";
    titleEl.textContent = title;
    const bodyEl = document.createElement("p");
    bodyEl.className = "text-xs mt-1 break-words";
    bodyEl.textContent = body;
    toast.append(titleEl, bodyEl);
    $("toast-container").appendChild(toast);
    setTimeout(() => toast.remove(), 6000);
}

function renderRoomsList() {
    const container = $("rooms-list-container");
    container.replaceChildren();

    if (state.rooms.length === 0) {
        const empty = document.createElement("p");
        empty.className = "text-sm text-gray-400 italic";
        empty.textContent = "No rooms joined yet";
        container.appendChild(empty);
        return;
    }

    for (const room of state.rooms) {
        const isActive = room.id === state.activeRoomId;
        const row = document.createElement("div");
        row.className = `p-3 border-l-4 rounded-r-md cursor-pointer transition flex items-center justify-between ${
            isActive ? "bg-indigo-50 border-indigo-600 text-indigo-900" : "hover:bg-gray-50 border-transparent text-gray-700"
        }`;
        row.onclick = () => selectRoom(room.id);

        const info = document.createElement("div");
        info.className = "flex-grow truncate mr-2";
        const name = document.createElement("p");
        name.className = "text-sm font-semibold truncate";
        name.textContent = room.name || `Room ${room.id.substring(0, 8)}`;
        const kind = document.createElement("span");
        kind.className = "text-[10px] text-gray-400 block";
        kind.textContent = room.is_group ? "Group room" : "Direct message";
        info.append(name, kind);

        const unread = document.createElement("span");
        unread.id = `unread-${room.id}`;
        unread.className = "h-2.5 w-2.5 bg-indigo-600 rounded-full flex-shrink-0 hidden";
        row.append(info, unread);
        container.appendChild(row);
    }
}

function setUnread(roomId, unread) {
    const badge = $(`unread-${roomId}`);
    if (badge) badge.classList.toggle("hidden", !unread);
}

function renderRoomHeader(room) {
    $("no-chat-selected").classList.toggle("hidden", Boolean(room));
    if (!room) return;
    $("active-room-title").textContent = room.name || `Room ${room.id}`;
    $("active-room-desc").textContent = room.is_group ? "Group room" : "Private 1:1 conversation";
    $("room-id-badge").textContent = `ID: ${room.id}`;
    $("leave-room-btn").classList.toggle("hidden", !room.is_group);
}

function clearMessages() {
    $("chat-messages").replaceChildren(Object.assign(document.createElement("div"), { id: "messages-anchor" }));
}

const STATUS_MARKS = { SENT: "✓", DELIVERED: "✓✓", READ: "✓✓ 👁" };

// The list uses flex-col-reverse: the first DOM child is shown at the bottom.
function renderMessage(msg, isNew) {
    const box = $("chat-messages");
    const isMe = state.currentUser && msg.sender_id === state.currentUser.id;

    const wrapper = document.createElement("div");
    wrapper.className = `flex w-full ${isMe ? "justify-end" : "justify-start"}`;
    const bubble = document.createElement("div");
    bubble.className = `max-w-xs md:max-w-md rounded-lg p-3 shadow-sm ${isMe ? "bg-indigo-600 text-white" : "bg-white text-gray-800"}`;

    const sender = document.createElement("p");
    sender.className = "text-[10px] font-bold mb-1 opacity-75";
    sender.textContent = isMe ? "You" : msg.sender_username;
    const content = document.createElement("p");
    content.className = "text-sm break-words whitespace-pre-wrap";
    content.textContent = msg.content;

    const footer = document.createElement("div");
    footer.className = "flex items-center justify-end gap-1.5 mt-1.5 opacity-60";
    const time = document.createElement("span");
    time.className = "text-[9px]";
    time.textContent = new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    footer.appendChild(time);
    if (isMe) {
        const mark = document.createElement("span");
        mark.id = `status-${msg.id}`;
        mark.className = "text-[10px] font-semibold";
        mark.textContent = STATUS_MARKS[msg.status] || "";
        footer.appendChild(mark);
    }

    bubble.append(sender, content, footer);
    wrapper.appendChild(bubble);

    if (isNew) {
        box.insertBefore(wrapper, $("messages-anchor").nextSibling);
        box.scrollTop = 0;
    } else {
        box.appendChild(wrapper);
    }
}

function updateMessageStatus(messageId, status) {
    const mark = $(`status-${messageId}`);
    if (mark) mark.textContent = STATUS_MARKS[status] || "";
}
