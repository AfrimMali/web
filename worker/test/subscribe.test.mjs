import assert from "node:assert/strict";
import test from "node:test";

import { handleRequest } from "../src/index.js";

const ENV = {
  ALLOWED_ORIGIN: "https://afrimmali.com",
  TURNSTILE_HOSTNAME: "afrimmali.com",
  TURNSTILE_SECRET_KEY: "test-secret",
  GOOGLE_FORM_ID: "1FAIpQLScorrect",
  GOOGLE_ENTRY_ID: "entry.123456789",
};

function submission(fields = {}, headers = {}) {
  return new Request("https://signal-newsletter-subscribe.example.workers.dev/", {
    method: "POST",
    headers: {
      "Accept": "application/json",
      "Origin": ENV.ALLOWED_ORIGIN,
      "Content-Type": "application/x-www-form-urlencoded",
      ...headers,
    },
    body: new URLSearchParams({
      email: "reader@example.com",
      "cf-turnstile-response": "valid-token",
      website: "",
      ...fields,
    }),
  });
}

function successfulUpstreams(calls) {
  return async (url, options) => {
    calls.push({ url: String(url), options });
    if (String(url).includes("siteverify")) {
      return Response.json({
        success: true,
        hostname: "afrimmali.com",
        action: "newsletter_subscribe",
      });
    }
    return new Response("stored", { status: 200 });
  };
}

test("rejects incomplete server configuration", async () => {
  const response = await handleRequest(submission(), {}, async () => {
    throw new Error("must not fetch");
  });
  assert.equal(response.status, 503);
});

test("answers an allowed CORS preflight narrowly", async () => {
  const request = new Request("https://worker.example/", {
    method: "OPTIONS",
    headers: { Origin: ENV.ALLOWED_ORIGIN },
  });
  const response = await handleRequest(request, ENV);
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), ENV.ALLOWED_ORIGIN);
  assert.equal(response.headers.get("Access-Control-Allow-Methods"), "POST, OPTIONS");
});

test("rejects submissions from another origin before any upstream call", async () => {
  let fetched = false;
  const response = await handleRequest(
    submission({}, { Origin: "https://attacker.example" }),
    ENV,
    async () => { fetched = true; },
  );
  assert.equal(response.status, 403);
  assert.equal(fetched, false);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), null);
});

test("validates the email and token before contacting an upstream", async () => {
  let fetched = false;
  const badEmail = await handleRequest(submission({ email: "not-an-email" }), ENV, async () => {
    fetched = true;
  });
  assert.equal(badEmail.status, 400);
  assert.equal(fetched, false);

  const noToken = await handleRequest(submission({ "cf-turnstile-response": "" }), ENV, async () => {
    fetched = true;
  });
  assert.equal(noToken.status, 422);
  assert.equal(fetched, false);
});

test("a filled honeypot returns generic success without storing anything", async () => {
  let fetched = false;
  const response = await handleRequest(submission({ website: "https://spam.example" }), ENV, async () => {
    fetched = true;
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).ok, true);
  assert.equal(fetched, false);
});

test("requires Turnstile success, the expected hostname, and the expected action", async () => {
  for (const verification of [
    { success: false },
    { success: true, hostname: "attacker.example", action: "newsletter_subscribe" },
    { success: true, hostname: "afrimmali.com", action: "login" },
  ]) {
    let calls = 0;
    const response = await handleRequest(submission(), ENV, async () => {
      calls += 1;
      return Response.json(verification);
    });
    assert.equal(response.status, 422);
    assert.equal(calls, 1);
  }
});

test("stores only after verification and returns an authoritative success", async () => {
  const calls = [];
  const response = await handleRequest(submission(), ENV, successfulUpstreams(calls));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, message: "You’re subscribed. Thank you." });
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /turnstile\/v0\/siteverify$/);
  assert.match(calls[1].url, /docs\.google\.com\/forms\/d\/e\/1FAIpQLScorrect\/formResponse$/);
  assert.equal(calls[1].options.body.get("entry.123456789"), "reader@example.com");
  assert.equal(calls[1].options.body.has("cf-turnstile-response"), false);
});

test("does not claim success when Google rejects the write", async () => {
  let calls = 0;
  const response = await handleRequest(submission(), ENV, async () => {
    calls += 1;
    if (calls === 1) {
      return Response.json({
        success: true,
        hostname: "afrimmali.com",
        action: "newsletter_subscribe",
      });
    }
    return new Response("failed", { status: 500 });
  });
  assert.equal(response.status, 502);
  assert.equal((await response.json()).ok, false);
});

test("plain form submissions receive an HTML confirmation", async () => {
  const response = await handleRequest(
    submission({}, { Accept: "text/html" }),
    ENV,
    successfulUpstreams([]),
  );
  assert.equal(response.status, 200);
  assert.match(response.headers.get("Content-Type"), /^text\/html/);
  assert.match(await response.text(), /Subscription confirmed/);
});
