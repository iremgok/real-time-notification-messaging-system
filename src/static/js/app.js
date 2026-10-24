// Page wiring: auth flow, room actions, history loading and sending messages.
async function handleRegister(e) {
    e.preventDefault();
    const username = $("register-username").value.trim();
    const password = $("register-password").value;
    try {
        await api.register(username, $("register-email").value.trim(), password);
        showAuthMessage("Registered successfully! Logging in...", false);
        await loginWith(username, password);
    } catch (err) {
        showAuthMessage(err.message);
    }
}

async function handleLogin(e) {
    e.preventDefault();
    try {
        await loginWith($("login-username").value.trim(), $("login-password").value);
    } catch (err) {
        showAuthMessage(err.message);
    }
}

async function loginWith(username, password) {
    const data = await api.login(username, password);
    state.token = data.access_token;
    sessionStorage.setItem("chat_token", state.token);
    await initializeDashboard();
}

function handleLogout() {
    socket.disconnect();
    sessionStorage.removeItem("chat_token");
    Object.assign(state, { token: null, currentUser: null, activeRoomId: null, rooms: [] });
    showAuth();
}

async function initializeDashboard() {
    try {
        state.currentUser = await api.me();
        $("logged-user-display").textContent = state.currentUser.username;
        showApp();
        await syncRooms();
        socket.connect();
    } catch (err) {
        console.error(err);
        handleLogout();
    }
}

async function syncRooms() {
    try {
        state.rooms = await api.listRooms();
        renderRoomsList();
        if (state.activeRoomId && !state.rooms.some(r => r.id === state.activeRoomId)) {
            state.activeRoomId = null;
            renderRoomHeader(null);
        }
    } catch (err) {
        if (err.status === 401) handleLogout();
    }
}

async function runAction(inputId, action) {
    const input = $(inputId);
    const value = input.value.trim();
    if (!value) return;
    try {
        const room = await action(value);
        input.value = "";
        await syncRooms();
        if (room && room.id) await selectRoom(room.id);
    } catch (err) {
        showToast("Error", err.message, "warning");
    }
}

const createGroupRoom = () => runAction("new-room-name", api.createRoom);
const createDirectRoom = () => runAction("dm-target-username", api.openDirect);
const joinGroupById = () => runAction("join-room-id", api.joinRoom);

async function leaveActiveRoom() {
    if (!state.activeRoomId || !confirm("Leave this room?")) return;
    try {
        await api.leaveRoom(state.activeRoomId);
        state.activeRoomId = null;
        renderRoomHeader(null);
        await syncRooms();
    } catch (err) {
        showToast("Error", err.message, "warning");
    }
}

async function selectRoom(roomId) {
    state.activeRoomId = roomId;
    state.cursor = null;
    state.hasMore = false;
    renderRoomsList();
    setUnread(roomId, false);
    renderRoomHeader(state.rooms.find(r => r.id === roomId));
    clearMessages();
    await loadHistory();
}

async function loadHistory() {
    const roomId = state.activeRoomId;
    if (!roomId || state.loadingHistory) return;
    state.loadingHistory = true;
    try {
        const page = await api.history(roomId, state.cursor);
        if (roomId !== state.activeRoomId) return; // user switched rooms meanwhile
        state.cursor = page.next_cursor;
        state.hasMore = page.has_more;
        // Items are oldest -> newest; with flex-col-reverse older ones are appended last.
        for (const msg of [...page.items].reverse()) {
            renderMessage(msg, false);
            if (msg.sender_id !== state.currentUser.id && msg.status !== "READ") socket.ack(msg.id, "READ");
        }
    } catch (err) {
        showToast("Error", err.message, "warning");
    } finally {
        state.loadingHistory = false;
    }
}

function sendMessage(e) {
    e.preventDefault();
    const input = $("chat-input");
    const content = input.value.trim();
    if (!content || !state.activeRoomId) return;
    const sent = socket.send({
        type: "send_message",
        data: { room_id: state.activeRoomId, content, client_id: crypto.randomUUID() },
    });
    if (sent) input.value = "";
    else showToast("Offline", "Connection lost, reconnecting...", "warning");
}

// Infinite scroll: load older messages when the user reaches the top.
$("chat-messages").addEventListener("scroll", (event) => {
    const el = event.currentTarget;
    const distanceToTop = el.scrollHeight - el.clientHeight - Math.abs(el.scrollTop);
    if (state.hasMore && distanceToTop < 10) loadHistory();
});

document.addEventListener("DOMContentLoaded", () => {
    if (state.token) initializeDashboard();
    else showAuth();
});
