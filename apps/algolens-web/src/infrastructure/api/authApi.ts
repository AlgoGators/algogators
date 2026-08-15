import type { AuthResponse, User } from '../../domain/identity/user';
import { API_BASE_URL } from './httpClient';

export const DEV_MODE = import.meta.env.VITE_DEV_MODE === '1';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/**
 * Every auth endpoint shares the same fetch skeleton: same base URL and cookie
 * credentials (the session travels in an httpOnly cookie, sent/received only
 * when `credentials: 'include'` is set).
 */
async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_BASE_URL}${path}`, { credentials: 'include', ...init });
}

export interface SessionResult {
  user: User | null;
  status: number;
}

async function toSessionResult(response: Response): Promise<SessionResult> {
  if (!response.ok) {
    return { user: null, status: response.status };
  }

  const data: AuthResponse = await response.json();
  return { user: data.user, status: response.status };
}

/** Parse the user out of a login/register response, or throw the backend's error. */
async function toUser(response: Response, action: 'Login' | 'Registration'): Promise<User> {
  if (!response.ok) {
    const error = await response.json();
    console.error(`[AuthContext] ${action} failed with error:`, error);
    throw new Error(error.error || `${action} failed`);
  }

  const data: AuthResponse = await response.json();
  return data.user;
}

export async function verifySessionRequest(): Promise<SessionResult> {
  const response = await authFetch('/auth/verify', { method: 'GET' });
  return toSessionResult(response);
}

export async function devLoginRequest(): Promise<SessionResult> {
  const response = await authFetch('/auth/dev-login', {
    method: 'POST',
    headers: JSON_HEADERS,
  });
  return toSessionResult(response);
}

export async function loginRequest(email: string, password: string): Promise<User> {
  const response = await authFetch('/auth/login', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email, password }),
  });
  return toUser(response, 'Login');
}

export async function registerRequest(
  email: string,
  password: string,
  firstName: string,
  lastName: string
): Promise<User> {
  const response = await authFetch('/auth/register', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      email,
      password,
      first_name: firstName,
      last_name: lastName,
    }),
  });
  return toUser(response, 'Registration');
}

export async function logoutRequest(): Promise<void> {
  await authFetch('/auth/logout', { method: 'POST' });
}

export interface CheckEmailResult {
  ok: boolean;
  registered: boolean;
  message?: string;
}

/**
 * Ask the backend whether an email is pre-authorized and/or already registered.
 * Sent with cookie credentials like every other auth call.
 */
export async function checkEmailRequest(email: string): Promise<CheckEmailResult> {
  const response = await authFetch('/auth/check-email', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email }),
  });

  const data = await response.json();
  return { ok: response.ok, registered: Boolean(data.registered), message: data.message };
}
