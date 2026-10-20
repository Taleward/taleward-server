// Verwaltung: Knopf „Kopieren“ neben einem Code. Ohne JavaScript bleibt der Knopf verborgen; ein Tippen auf den
// Code markiert ihn ganz.
(function () {
  if (!navigator.clipboard) return;  // nur über https oder auf diesem PC
  document.querySelectorAll("button[data-kopieren]").forEach(function (knopf) {
    var ziel = document.getElementById(knopf.getAttribute("data-kopieren"));
    if (!ziel) return;
    var text = knopf.textContent;
    knopf.hidden = false;
    knopf.addEventListener("click", function () {
      navigator.clipboard.writeText(ziel.textContent.trim()).then(function () {
        knopf.textContent = knopf.getAttribute("data-fertig") || text;
        setTimeout(function () { knopf.textContent = text; }, 2000);
      });
    });
  });
})();
