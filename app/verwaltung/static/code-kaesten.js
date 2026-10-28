// Verwaltung und Guthaben-Seite (0.4.138): Code des zweiten Faktors in sechs Kästchen. Ohne JavaScript bleibt das
// eine Eingabefeld. Einfügen (Strg+V) und Vorschläge der Tastatur verteilen alle Ziffern auf einmal; Wiederherstellungs-
// codes gehen über „Stattdessen einen Wiederherstellungscode eingeben“ wieder in das eine Feld.
(function () {
  var STELLEN = 6;

  function ziffern(text) { return (text || "").replace(/\D/g, ""); }

  document.querySelectorAll("input[data-code-kaesten]").forEach(function (feld) {
    var form = feld.form;
    var mitCodes = feld.getAttribute("data-code-kaesten") !== "nur-ziffern";
    var muster = feld.getAttribute("data-ziffer") || "Ziffer {n} von 6";

    var reihe = document.createElement("div");
    reihe.className = "code-kaesten";
    reihe.setAttribute("role", "group");
    reihe.setAttribute("aria-label", feld.getAttribute("data-gruppe") || "");
    var kaesten = [];
    for (var i = 0; i < STELLEN; i++) {
      var k = document.createElement("input");
      k.type = "text";
      k.inputMode = "numeric";
      k.autocomplete = i === 0 ? "one-time-code" : "off";
      k.maxLength = i === 0 ? STELLEN : 1;  // das erste nimmt Vorschläge der Tastatur ganz an
      k.className = "code-kasten";
      k.setAttribute("aria-label", muster.replace("{n}", String(i + 1)));
      kaesten.push(k);
      reihe.appendChild(k);
    }

    function verteilen(text, ab) {
      var z = ziffern(text);
      for (var j = 0; j < z.length && ab + j < STELLEN; j++) kaesten[ab + j].value = z[j];
      var naechstes = Math.min(ab + z.length, STELLEN - 1);
      kaesten[naechstes].focus();
      sammeln();
    }

    function sammeln() {
      feld.value = kaesten.map(function (k) { return k.value; }).join("");
    }

    kaesten.forEach(function (k, idx) {
      k.addEventListener("input", function () {
        var wert = k.value;
        if (wert.length > 1 || ziffern(wert) !== wert) {
          k.value = "";
          verteilen(wert, idx);
          return;
        }
        sammeln();
        if (wert && idx < STELLEN - 1) kaesten[idx + 1].focus();
      });
      k.addEventListener("keydown", function (e) {
        if (e.key === "Backspace" && !k.value && idx > 0) {
          kaesten[idx - 1].value = "";
          kaesten[idx - 1].focus();
          sammeln();
          e.preventDefault();
        } else if (e.key === "ArrowLeft" && idx > 0) {
          kaesten[idx - 1].focus();
          e.preventDefault();
        } else if (e.key === "ArrowRight" && idx < STELLEN - 1) {
          kaesten[idx + 1].focus();
          e.preventDefault();
        }
      });
      k.addEventListener("paste", function (e) {
        var text = (e.clipboardData || window.clipboardData).getData("text");
        e.preventDefault();
        if (mitCodes && /[a-z]/i.test(text)) {  // eingefügter Wiederherstellungscode: zurück zum einen Feld
          zeileZeigen(text.trim());
          return;
        }
        kaesten.forEach(function (x) { x.value = ""; });
        verteilen(text, 0);
      });
      k.addEventListener("focus", function () { k.select(); });
    });

    var umschalter = null;
    function kaestenZeigen() {
      feld.classList.add("unsichtbar");
      feld.required = false;
      feld.tabIndex = -1;
      reihe.hidden = false;
      if (umschalter) umschalter.hidden = false;
    }
    function zeileZeigen(wert) {
      reihe.hidden = true;
      feld.classList.remove("unsichtbar");
      feld.required = true;
      feld.tabIndex = 0;
      feld.value = wert || "";
      if (umschalter) umschalter.hidden = true;
      feld.focus();
    }

    feld.insertAdjacentElement("afterend", reihe);  // im Label: die Beschriftung steht über den Kästchen
    if (feld.parentElement && feld.parentElement.tagName === "LABEL") feld.parentElement.classList.add("mit-code-kaesten");
    if (mitCodes && feld.getAttribute("data-umschalten")) {
      umschalter = document.createElement("button");
      umschalter.type = "button";
      umschalter.className = "tw-btn tw-btn--quiet tw-btn--klein code-kaesten__umschalten";
      umschalter.textContent = feld.getAttribute("data-umschalten");
      umschalter.addEventListener("click", function () { zeileZeigen(""); });
      reihe.insertAdjacentElement("afterend", umschalter);
    }
    kaestenZeigen();
    if (feld.autofocus || document.activeElement === feld) kaesten[0].focus();

    form.addEventListener("submit", function (e) {
      if (reihe.hidden) return;  // Wiederherstellungscode im einen Feld
      sammeln();
      if (feld.value.length !== STELLEN) {
        e.preventDefault();
        var leer = kaesten.filter(function (k) { return !k.value; })[0] || kaesten[0];
        leer.focus();
      }
    });
  });
})();
