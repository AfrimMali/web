const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

function deploymentConfig(env) {
  const required = [
    "ALLOWED_ORIGIN",
    "TURNSTILE_SECRET_KEY",
    "GOOGLE_FORM_ID",
    "GOOGLE_ENTRY_ID",
  ];
  if (required.some((name) => !String(env[name] || "").trim())) {
    return null;
  }

  let allowed;
  try {
    allowed = new URL(env.ALLOWED_ORIGIN);
  } catch (_) {
    return null;
  }
  if (allowed.protocol !== "https:" || allowed.username || allowed.password) {
    return null;
  }

  const formId = String(env.GOOGLE_FORM_ID).trim();
  const entryId = String(env.GOOGLE_ENTRY_ID).trim();
  if (!/^[A-Za-z0-9_-]+$/.test(formId) || !/^entry\.\d+$/.test(entryId)) {
    return null;
  }

  return {
    allowedOrigin: allowed.origin,
    expectedHostname: String(env.TURNSTILE_HOSTNAME || allowed.hostname).trim(),
    turnstileSecret: String(env.TURNSTILE_SECRET_KEY).trim(),
    formId,
    entryId,
  };
}

function isEmail(value) {
  return (
    typeof value === "string" &&
    value.length <= 254 &&
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)
  );
}

function commonHeaders(origin, allowCors) {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  });
  if (allowCors) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Vary", "Origin");
  }
  return headers;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function result(request, config, status, ok, message, allowCors = true) {
  const headers = commonHeaders(config.allowedOrigin, allowCors);
  if ((request.headers.get("Accept") || "").includes("application/json")) {
    headers.set("Content-Type", "application/json; charset=utf-8");
    return new Response(JSON.stringify({ ok, message }), { status, headers });
  }

  headers.set("Content-Type", "text/html; charset=utf-8");
  const title = ok ? "Subscription confirmed" : "Subscription not completed";
  const body = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>${title}</title></head><body><main><h1>${title}</h1>
<p>${escapeHtml(message)}</p><p><a href="${escapeHtml(config.allowedOrigin)}/subscribe.html">Return to Signal</a></p>
</main></body></html>`;
  return new Response(body, { status, headers });
}

async function verifyTurnstile(token, request, config, fetcher) {
  const body = new URLSearchParams({
    secret: config.turnstileSecret,
    response: token,
  });
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) body.set("remoteip", ip);

  let response;
  let verification;
  try {
    response = await fetcher(SITEVERIFY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok) return false;
    verification = await response.json();
  } catch (_) {
    return false;
  }

  return (
    verification.success === true &&
    verification.hostname === config.expectedHostname &&
    verification.action === "newsletter_subscribe"
  );
}

async function storeInGoogleForm(email, config, fetcher) {
  const body = new URLSearchParams({ [config.entryId]: email });
  try {
    const response = await fetcher(
      `https://docs.google.com/forms/d/e/${config.formId}/formResponse`,
      {
        method: "POST",
        headers: {
          "Accept": "text/html",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body,
        redirect: "follow",
      },
    );
    return response.ok;
  } catch (_) {
    return false;
  }
}

export async function handleRequest(request, env, fetcher = fetch) {
  const config = deploymentConfig(env);
  if (!config) {
    const fallback = {
      allowedOrigin: "https://afrimmali.com",
    };
    return result(
      request,
      fallback,
      503,
      false,
      "Subscription is not configured yet. Please try again later.",
      false,
    );
  }

  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/health") {
    return result(request, config, 200, true, "ready", false);
  }

  const requestOrigin = request.headers.get("Origin");
  const originAllowed = requestOrigin === config.allowedOrigin;
  if (request.method === "OPTIONS") {
    if (!originAllowed) {
      return result(request, config, 403, false, "Request origin is not allowed.", false);
    }
    const headers = commonHeaders(config.allowedOrigin, true);
    headers.set("Access-Control-Allow-Headers", "Accept, Content-Type");
    headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
    headers.set("Access-Control-Max-Age", "86400");
    return new Response(null, { status: 204, headers });
  }

  if (request.method !== "POST") {
    return result(request, config, 405, false, "Method not allowed.", originAllowed);
  }
  if (!originAllowed) {
    return result(request, config, 403, false, "Request origin is not allowed.", false);
  }

  const contentLength = Number(request.headers.get("Content-Length") || 0);
  if (contentLength > 12_000) {
    return result(request, config, 413, false, "The submission is too large.");
  }

  let form;
  try {
    form = await request.formData();
  } catch (_) {
    return result(request, config, 400, false, "The submission could not be read.");
  }

  // Bots that fill the off-screen field get the same success response but are
  // never sent to Turnstile or Google. Do not reveal the trap to the submitter.
  if (String(form.get("website") || "").trim()) {
    return result(request, config, 200, true, "You’re subscribed. Thank you.");
  }

  const email = String(form.get("email") || "").trim();
  if (!isEmail(email)) {
    return result(request, config, 400, false, "Enter a valid email address.");
  }

  const token = String(form.get("cf-turnstile-response") || "");
  if (!token || token.length > 2048) {
    return result(request, config, 422, false, "Complete the spam check and try again.");
  }

  if (!(await verifyTurnstile(token, request, config, fetcher))) {
    return result(request, config, 422, false, "The spam check expired or failed. Please try again.");
  }

  if (!(await storeInGoogleForm(email, config, fetcher))) {
    return result(
      request,
      config,
      502,
      false,
      "Subscription is temporarily unavailable. Please try again.",
    );
  }

  return result(request, config, 200, true, "You’re subscribed. Thank you.");
}

export default {
  fetch(request, env) {
    return handleRequest(request, env);
  },
};
