// Verwaltung und Guthaben: Passkeys (WebAuthn). Ohne JavaScript oder ohne Unterstützung im Browser bleibt alles beim
// Code aus der Authenticator-App. Hier passiert nur: Optionen vom Server lesen (data-Attribut bzw. fetch von der
// eigenen Herkunft), navigator.credentials.create/get aufrufen, die Antwort als JSON in ein verborgenes Feld schreiben
// und das Formular abschicken. Kein innerHTML, keine fremden Adressen.
(function () {
  "use strict";
  if (!window.PublicKeyCredential || !navigator.credentials) return;

  function b64url(buf) {
    var bytes = new Uint8Array(buf), s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function unb64url(text) {
    var s = String(text).replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  function antwortJson(cred, art) {
    var r = cred.response, aus = {
      id: cred.id, rawId: b64url(cred.rawId), type: cred.type,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      authenticatorAttachment: cred.authenticatorAttachment || null,
      response: { clientDataJSON: b64url(r.clientDataJSON) }
    };
    if (art === "create") {
      aus.response.attestationObject = b64url(r.attestationObject);
      if (r.getTransports) { try { aus.response.transports = r.getTransports(); } catch (e) { /* egal */ } }
    } else {
      aus.response.authenticatorData = b64url(r.authenticatorData);
      aus.response.signature = b64url(r.signature);
      aus.response.userHandle = r.userHandle ? b64url(r.userHandle) : null;
    }
    return JSON.stringify(aus);
  }

  function optionenLesen(json, art) {
    var o = JSON.parse(json);
    o.challenge = unb64url(o.challenge);
    if (art === "create") {
      o.user.id = unb64url(o.user.id);
      (o.excludeCredentials || []).forEach(function (c) { c.id = unb64url(c.id); });
    } else {
      (o.allowCredentials || []).forEach(function (c) { c.id = unb64url(c.id); });
    }
    return o;
  }

  function melden(knopf, text) {
    var ziel = document.getElementById(knopf.getAttribute("data-meldung") || "");
    if (ziel) ziel.textContent = text || "";
  }

  // Anmelden / Bestätigen: Optionen stehen im data-Attribut des Knopfes, die Antwort geht in das Feld „passkey“
  document.querySelectorAll("button[data-passkey-anmelden]").forEach(function (knopf) {
    var form = knopf.form, feld = form && form.querySelector("input[name=passkey]");
    if (!form || !feld || !knopf.getAttribute("data-passkey-anmelden")) return;
    knopf.hidden = false;
    knopf.addEventListener("click", function (ev) {
      ev.preventDefault();
      knopf.disabled = true;
      melden(knopf, knopf.getAttribute("data-warte") || "");
      var o;
      try { o = optionenLesen(knopf.getAttribute("data-passkey-anmelden"), "get"); } catch (e) { knopf.disabled = false; return; }
      navigator.credentials.get({ publicKey: o }).then(function (cred) {
        feld.value = antwortJson(cred, "get");
        var code = form.querySelector("input[name=code]");
        if (code) code.required = false;
        form.submit();
      }).catch(function () {
        knopf.disabled = false;
        melden(knopf, knopf.getAttribute("data-fehler") || "");
      });
    });
  });

  // Einrichten: Optionen vom Server holen (POST mit CSRF), create, Antwort und Name abschicken
  document.querySelectorAll("button[data-passkey-einrichten]").forEach(function (knopf) {
    var form = knopf.form, feld = form && form.querySelector("input[name=antwort]");
    if (!form || !feld) return;
    knopf.hidden = false;
    knopf.addEventListener("click", function (ev) {
      ev.preventDefault();
      knopf.disabled = true;
      melden(knopf, knopf.getAttribute("data-warte") || "");
      var daten = new FormData();
      daten.append("csrf", form.querySelector("input[name=csrf]").value);
      fetch(knopf.getAttribute("data-passkey-einrichten"), { method: "POST", body: daten, credentials: "same-origin",
                                                             headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw j; return j; }); })
        .then(function (j) {
          var o = optionenLesen(JSON.stringify(j), "create");
          return navigator.credentials.create({ publicKey: o });
        })
        .then(function (cred) {
          feld.value = antwortJson(cred, "create");
          form.submit();
        })
        .catch(function (f) {
          knopf.disabled = false;
          var text = knopf.getAttribute("data-fehler") || "";
          if (f && f.fehler === "zu_viele") text = knopf.getAttribute("data-zu-viele") || text;
          melden(knopf, text);
        });
    });
  });
})();
