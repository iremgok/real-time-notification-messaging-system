// REST client + shared client state.
const API_PREFIX = "/api/v1";

const state = {
    token: sessionStorage.getItem("chat_token"),
    currentUser: null,
    activeRoomId: null,
    rooms: [],
    cursor: null,
    hasMore: false,
    loadingHistory: false,
};

class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

async function apiRequest(path, { method = "GET", body, form } = {}) {
    const headers = {};
    if (state.token) headers["Authorization"] = `Bearer ${state.token}`;

    let payload;
    if (form) {
        headers["Content-Type"] = "application/x-www-form-urlencoded";
        payload = new URLSearchParams(form);
    } else if (body !== undefined) {
        headers["Content-Type"] = "application/json";
        payload = JSON.stringify(body);
    }

    const res = await fetch(`${API_PREFIX}${path}`, { method, headers, body: payload });
    if (res.status === 204) return null;

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = Array.isArray(data.detail)
            ? data.detail.map(d => d.msg).join(", ")
            : data.detail || `Request failed (${res.status})`;
        throw new ApiError(res.status, detail);
    }
    return data;
}

const api = {
    register: (username, email, password) =>
        apiRequest("/auth/register", { method: "POST", body: { username, email, password } }),
    login: (username, password) =>
        apiRequest("/auth/login", { method: "POST", form: { username, password } }),
    me: () => apiRequest("/auth/me"),
    listRooms: () => apiRequest("/rooms"),
    createRoom: (name) => apiRequest("/rooms", { method: "POST", body: { name } }),
    openDirect: (targetUsername) =>
        apiRequest("/rooms/direct", { method: "POST", body: { target_username: targetUsername } }),
    joinRoom: (roomId) => apiRequest(`/rooms/${encodeURIComponent(roomId)}/join`, { method: "POST" }),
    leaveRoom: (roomId) => apiRequest(`/rooms/${encodeURIComponent(roomId)}/leave`, { method: "POST" }),
    history: (roomId, cursor) => {
        const params = new URLSearchParams({ limit: "25" });
        if (cursor) params.set("cursor", cursor);
        return apiRequest(`/messages/${encodeURIComponent(roomId)}?${params}`);
    },
};
