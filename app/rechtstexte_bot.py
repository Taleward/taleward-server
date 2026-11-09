"""Datenschutzerklärung und Nutzungsbedingungen des Discord-Bots (0.4.162) – unter /discord-bot/datenschutz und
/discord-bot/nutzungsbedingungen, gepflegt unter Verwaltung → Discord-Bot → Rechtstexte.

VORLAGEN, keine Rechtsberatung. Der Wortlaut folgt dem vom Betreiber des Bots freigegebenen Text; Betreiber-Angaben
(Name, Anschrift, Kontakt) kommen aus den Angaben in der Verwaltung, die Adressen aus der öffentlichen Adresse dieses
Servers. Was nur der Betreiber weiß (Aufsichtsbehörde, Hoster des Bots, Ort des Rechenzentrums, Frist der
Sicherungen), steht in [eckigen Klammern]; veröffentlicht werden kann erst ohne offene Klammer (sonst bleibt die Seite
404). Gespeichert wird wie bei den übrigen Rechtstexten (`rechtstexte.speichern`).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

ARTEN = ("bot_datenschutz", "bot_nutzung")
PFADE = {"bot_datenschutz": "/discord-bot/datenschutz", "bot_nutzung": "/discord-bot/nutzungsbedingungen"}


def _angaben(db: Session, en: bool) -> dict:
    from app import betreiber
    from app.einstellungen import oeffentliche_adresse

    b = betreiber.lesen(db)
    url = (oeffentliche_adresse(db) or "").rstrip("/") or ("[Address of this server]" if en else "[Adresse dieses Servers]")
    zeilen = [z.strip() for z in (b.anschrift or "").split("\n") if z.strip()]
    return {
        "name": b.name or ("[Name of the operator]" if en else "[Name des Betreibers]"),
        "anschrift": "\n".join(zeilen) or ("[Postal address]" if en else "[Anschrift]"),
        "anschrift_zeile": ", ".join(zeilen) or ("[Postal address]" if en else "[Anschrift]"),
        "kontakt": b.kontakt or ("[E-mail address]" if en else "[E-Mail-Adresse]"),
        "url": url,
        "server": url.split("://", 1)[-1],
        "aufsicht": ("[Data protection supervisory authority responsible for you, with address]" if en
                     else "[Für dich zuständige Datenschutz-Aufsichtsbehörde mit Anschrift]"),
        "hoster": ("[Name and address of the bot's hosting provider]" if en
                   else "[Name und Anschrift des Hosters des Bots]"),
        "ort": "[Location of the data centre]" if en else "[Ort des Rechenzentrums]",
        "tage": "[7]",
    }


def vorlage(db: Session, art: str, sprache: str = "de") -> str:
    en = sprache == "en"
    a = _angaben(db, en)
    if art == "bot_datenschutz":
        return (_DS_EN if en else _DS_DE).format(**a)
    if art == "bot_nutzung":
        return (_NB_EN if en else _NB_DE).format(**a)
    raise KeyError(art)


_DS_DE = """## Wer verantwortlich ist

Den Bot „Taleward“ auf Discord betreibt:

{name}
{anschrift}
E-Mail: {kontakt}

Das ist dieselbe Person wie im Impressum unter {url}/impressum. Ein Datenschutzbeauftragter ist nicht bestellt, weil die gesetzlichen Voraussetzungen dafür nicht vorliegen.

## Worum es geht

Der Bot nimmt Spielabende in Discord-Sprachkanälen auf, eine Tonspur je Person, und bringt die Spuren zum Taleward-Server eurer Gruppe. Dort entsteht daraus ein Kapitel. Welcher Server das ist, bestimmt eure Spielleitung beim Verbinden der Kampagne. Für das, was auf diesem Server mit den Spuren geschieht, gilt die Datenschutzerklärung dieses Servers, nicht diese hier. Das gilt auch, wenn eure Gruppe den von uns betriebenen Server {server} nutzt; dessen Hinweis findet ihr unter {url}/datenschutz.

Außerdem bietet der Bot auf unserem eigenen Discord-Server kleine Helfer an (häufige Fragen, Stand der Software, ein Rückmeldeformular, einen Knopf für die Rolle „Tester“).

## Was der Bot verarbeitet und warum

1. Aufnahme im Sprachkanal. Startet die Spielleitung eine Aufnahme, verarbeitet der Bot:

- die Discord-Kennung und den Anzeigenamen jeder Person im Kanal, um zu prüfen, wer zugestimmt hat, und um die Liste „Aufnahme läuft: …“ im Kanal zu zeigen;
- die Stimme ausschließlich der Personen, die selbst zugestimmt haben, als eigene Tonspur je Person;
- Zeitpunkte von Start, Pause, Weiter und Ende sowie die Kennung des Sprachkanals, um die Aufnahme zu steuern.

Die Stimme einer Person, die nicht zugestimmt hat, nimmt der Bot nicht entgegen; ihre Datenpakete werden verworfen, bevor sie entschlüsselt werden. Wer nichts tut, wird nicht aufgenommen.

Um zu wissen, wer zugestimmt hat, fragt der Bot beim Start und dann alle 30 Sekunden den Taleward-Server der Gruppe: Er schickt die Discord-Kennungen aller Personen im Kanal, auch derer, die nicht zugestimmt haben, und bekommt je Kennung nur zurück, ob die Person bereit ist, verknüpfen muss oder zustimmen muss. Namen gehen dabei nicht mit, und der Server gibt keine Namen heraus.

Rechtsgrundlage ist die Einwilligung jeder aufgenommenen Person (Art. 6 Abs. 1 lit. a DSGVO), erteilt in der Taleward-App oder über den Zustimmungslink des Bots. Wer als Mitglied der Kampagne zustimmt, stimmt für alle Aufnahmen dieser Kampagne bis zum Widerruf zu, wie am Spieltisch; wer als Gast über den Link zustimmt, nur für diese eine Runde. Die Verarbeitung der Kennungen und Anzeigenamen aller Personen im Kanal und die Abfrage beim Taleward-Server stützen sich auf unser berechtigtes Interesse, die Aufnahme auf die zugestimmten Personen zu beschränken und das für alle sichtbar zu machen (Art. 6 Abs. 1 lit. f DSGVO).

2. Verbinden und Zustimmen. Beim Verbinden einer Kampagne schickt der Bot die Discord-Kennung der Spielleitung, die Kennung und den Namen des Discord-Servers an den Taleward-Server der Gruppe. Beim Zustimmen schickt er die Discord-Kennung und den Anzeigenamen der Person als Vorschlag für den Gastnamen. Die Links, die der Bot dafür ausgibt, sind nur für die jeweilige Person sichtbar, einmalig und 15 Minuten gültig. Rechtsgrundlage: Durchführung dessen, was ihr mit dem Bot machen wollt (Art. 6 Abs. 1 lit. b DSGVO).

3. Stelle merken. Wer aufgenommen wird, kann während der Runde mit /taleward merken eine Stelle für sich markieren, wie „Merken“ in der App. Deine Discord-Kennung, der Zeitpunkt und, wenn du eine eingibst, deine Notiz gehen dabei wie jeder Discord-Befehl über Discord zum Bot und vom Bot an den Taleward-Server der Gruppe; der speichert die Stelle nur für dich, wie in der App beschrieben. Der Bot selbst behält davon nichts, auch die Notiz nicht; die Antwort sieht nur du. Rechtsgrundlage: dein Wunsch, dir die Stelle zu merken (Art. 6 Abs. 1 lit. b DSGVO).

4. Hochladen. Nach dem Ende gehen die Spuren zum Taleward-Server der Gruppe. Kennungen der aufgenommenen Personen werden dabei nur so übertragen, wie der Server sie beim Zustimmen vergeben hat.

5. Helfer auf unserem Discord-Server. Das Rückmeldeformular (/rueckmeldung) legt deinen Text zusammen mit deinem Discord-Benutzernamen und deiner Discord-Kennung in einem privaten Kanal ab, den nur der Betreiber sieht; freiwillig kannst du eine Kontaktmöglichkeit angeben. Der Knopf „Tester werden“ vergibt oder entfernt eine Rolle auf dem Server. Die Befehle für häufige Fragen und den Stand der Software verarbeiten nichts über dich. Rechtsgrundlage: dein Wunsch, uns etwas mitzuteilen bzw. die Rolle zu bekommen (Art. 6 Abs. 1 lit. b DSGVO).

## Wie lange der Bot speichert

- Tonspuren: nur bis der Taleward-Server der Gruppe den Empfang bestätigt hat, danach sofort gelöscht. Klappt das Hochladen nicht, versucht der Bot es bis zu 24 Stunden nach Beginn der Aufnahme und löscht die Spuren dann.
- Solange die Spuren beim Bot liegen, sind sie verschlüsselt. Öffnen kann sie nur der Taleward-Server eurer Gruppe; weder der Bot selbst noch wir als Betreiber des Bots können sie hier anhören. Was auf dem Server eurer Gruppe geschieht, regelt dessen Datenschutzerklärung.
- Bricht die Spielleitung die Aufnahme ab (/taleward verwerfen), löscht der Bot die Spuren sofort; nichts wird hochgeladen, der Taleward-Server erfährt nur, dass die Runde verworfen wurde.
- Widerrufst du deine Zustimmung während der Aufnahme (in der App), schließt der Bot deine Spur spätestens nach etwa 30 Sekunden und löscht sie; der Taleward-Server verwirft auch, was von deiner Spur schon angekommen ist; dein Name verschwindet aus der Liste im Kanal.
- Kennungen, Namen und Zeitpunkte einer Aufnahme hält der Bot nur im Arbeitsspeicher, solange die Aufnahme läuft und bis die Spuren hochgeladen sind. Die Spurdateien und ihre Begleitdatei enthalten keine Namen und keine Discord-Kennungen, nur die Kennung, die der Taleward-Server beim Zustimmen vergeben hat; den Dateinamen (Nummer der Spur) nehmen sie beim Hochladen mit.
- Rückmeldungen bleiben im privaten Kanal, bis sie bearbeitet sind, höchstens 12 Monate.
- Der Bot führt keine Protokolle mit Namen, Kennungen oder Inhalten.

Werden beim Hoster Sicherungen des Servers angelegt, auf dem der Bot läuft, können Spuren, die gerade beim Bot liegen, darin bis zu {tage} Tage enthalten sein, ebenfalls nur verschlüsselt; die Sicherungen sind nur für den Betreiber zugänglich und werden danach überschrieben.

## Wer die Daten bekommt

- Discord (Discord Netherlands B.V. für Nutzer in der EU): Sprache, Namen, Kennungen und Befehle laufen über Discords Dienste, bevor der Bot sie sieht; dafür gelten Discords eigene Datenschutzbestimmungen. Discord verschlüsselt Sprachverbindungen zwischen den Teilnehmenden und dem Bot.
- Der Taleward-Server eurer Gruppe: bekommt die Spuren, die Discord-Kennungen aller Personen im Kanal für die Abfrage der Zustimmung (Abschnitt 1), die in Abschnitt 2 genannten Angaben und gemerkte Stellen (Abschnitt 3). Wer ihn betreibt, sagt euch die Spielleitung; die App zeigt es unter „Kampagne verwalten → Deine Daten“.
- Hoster des Bots: {hoster}, Rechenzentrum in {ort}; mit Vertrag zur Auftragsverarbeitung. Der Hoster erhält keine Daten zur eigenen Nutzung.
- Sonst niemand. Wir geben nichts weiter und verkaufen nichts.

Eine Übermittlung in Länder außerhalb der EU findet durch uns nicht statt. Ob Discord Daten außerhalb der EU verarbeitet, regelt Discord in seinen eigenen Bestimmungen.

## Deine Rechte

Du hast das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit und Widerspruch (Art. 15–21 DSGVO). Eine Einwilligung kannst du jederzeit mit Wirkung für die Zukunft widerrufen, am einfachsten in der Taleward-App unter „Kampagne verwalten → Deine Daten“; während einer laufenden Aufnahme wirkt der Widerruf spätestens nach etwa 30 Sekunden. Für alles andere schreib an {kontakt}. Du kannst dich bei einer Datenschutz-Aufsichtsbehörde beschweren; für uns zuständig ist {aufsicht}.

## Was wir nicht tun

Keine Werbung, kein Tracking, keine Weitergabe an Dritte, keine Auswertung von Textnachrichten (der Bot liest keine Nachrichten in euren Kanälen), keine Stimmprofile auf dem Bot.

## Änderungen

Ändert sich etwas am Bot, passen wir diesen Text an und vermerken das Datum unten. Die jeweils gültige Fassung steht unter {url}/discord-bot/datenschutz."""

_DS_EN = """## Who is responsible

The “Taleward” bot on Discord is operated by:

{name}
{anschrift}
Email: {kontakt}

This is the same person as in the legal notice at {url}/impressum. No data protection officer has been appointed because the legal requirements for one are not met.

## What this is about

The bot records game nights in Discord voice channels, one audio track per person, and delivers the tracks to your group’s Taleward server. There they become a chapter. Which server that is, your game master decides when connecting the campaign. What happens with the tracks on that server is covered by that server’s privacy policy, not by this one. That also applies if your group uses the server we operate, {server}; its policy is at {url}/datenschutz.

On our own Discord server the bot also offers small helpers (frequently asked questions, software status, a feedback form, a button for the “Tester” role).

## What the bot processes and why

1. Recording in the voice channel. When the game master starts a recording, the bot processes:

- the Discord ID and display name of every person in the channel, to check who has consented and to show the list “Recording: …” in the channel;
- the voice of those people only who have consented themselves, as a separate audio track per person;
- the times of start, pause, resume and end, and the ID of the voice channel, to control the recording.

The voice of a person who has not consented is not accepted by the bot; their data packets are discarded before they are decrypted. If you do nothing, you are not recorded.

To know who has consented, the bot asks your group’s Taleward server at the start and then every 30 seconds: it sends the Discord IDs of everyone in the channel, including those who have not consented, and receives per ID only whether that person is ready, needs to link or needs to consent. No names are sent, and the server returns no names.

The legal basis is the consent of each recorded person (Art. 6(1)(a) GDPR), given in the Taleward app or via the bot’s consent link. Those who consent as members of the campaign consent to all recordings of that campaign until withdrawal, as at the table; those who consent as guests via the link, to this one session only. Processing the IDs and display names of everyone in the channel and the query to the Taleward server are based on our legitimate interest in limiting the recording to consenting people and making that visible to all (Art. 6(1)(f) GDPR).

2. Connecting and consenting. When a campaign is connected, the bot sends the game master’s Discord ID and the ID and name of the Discord server to the group’s Taleward server. When someone consents, it sends that person’s Discord ID and display name as a suggested guest name. The links the bot issues for this are visible only to the person concerned, single-use and valid for 15 minutes. Legal basis: carrying out what you want to do with the bot (Art. 6(1)(b) GDPR).

3. Bookmarking a moment. Anyone being recorded can mark a moment for themselves during the session with /taleward mark, like “Bookmark” in the app. Your Discord ID, the time and, if you enter one, your note travel, like every Discord command, through Discord to the bot and from the bot to your group’s Taleward server, which stores the bookmark for you only, as described in the app. The bot itself keeps none of it, not even the note; only you see the reply. Legal basis: your wish to bookmark the moment (Art. 6(1)(b) GDPR).

4. Uploading. After the end, the tracks go to the group’s Taleward server. IDs of recorded people are transmitted only in the form the server assigned when they consented.

5. Helpers on our Discord server. The feedback form (/feedback) stores your text together with your Discord username and Discord ID in a private channel visible only to the operator; you may add a way to contact you. The “Become a tester” button assigns or removes a role on the server. The commands for frequently asked questions and software status process nothing about you. Legal basis: your wish to tell us something or to get the role (Art. 6(1)(b) GDPR).

## How long the bot stores data

- Audio tracks: only until your group’s Taleward server has confirmed receipt, then deleted immediately. If uploading fails, the bot keeps trying for up to 24 hours after the recording started and then deletes the tracks.
- While the tracks are with the bot, they are encrypted. Only your group’s Taleward server can open them; neither the bot itself nor we as the bot’s operator can listen to them here. What happens on your group’s server is governed by that server’s privacy policy.
- If the game master aborts the recording (/taleward discard), the bot deletes the tracks immediately; nothing is uploaded, the Taleward server only learns that the session was discarded.
- If you withdraw your consent during the recording (in the app), the bot closes your track within about 30 seconds and deletes it; the Taleward server also discards whatever of your track has already arrived; your name disappears from the list in the channel.
- IDs, names and times of a recording are held only in memory while the recording runs and until the tracks are uploaded. The track files and their companion file contain no names and no Discord IDs, only the ID the Taleward server assigned when the person consented; the file name (track number) goes along when uploading.
- Feedback stays in the private channel until it has been handled, at most 12 months.
- The bot keeps no logs containing names, IDs or content.

If the hosting provider creates backups of the server the bot runs on, tracks that are with the bot at that moment may be contained in them for up to {tage} days, likewise only in encrypted form; backups are accessible only to the operator and are overwritten afterwards.

## Who receives the data

- Discord (Discord Netherlands B.V. for users in the EU): voice, names, IDs and commands pass through Discord’s services before the bot sees them; Discord’s own privacy policy applies. Discord encrypts voice connections between participants and the bot.
- Your group’s Taleward server: receives the tracks, the Discord IDs of everyone in the channel for the consent query (section 1), the details named in section 2 and bookmarks (section 3). Your game master can tell you who operates it; the app shows it under “Manage campaign → Your data”.
- The bot’s hosting provider: {hoster}, data centre in {ort}; under a data processing agreement. The provider receives no data for its own use.
- Nobody else. We do not pass anything on and do not sell anything.

We do not transfer data to countries outside the EU. Whether Discord processes data outside the EU is governed by Discord’s own policies.

## Your rights

You have the right of access, rectification, erasure, restriction of processing, data portability and objection (Art. 15–21 GDPR). You can withdraw consent at any time with effect for the future, most easily in the Taleward app under “Manage campaign → Your data”; during a running recording the withdrawal takes effect within about 30 seconds. For anything else, write to {kontakt}. You can complain to a data protection supervisory authority; the one responsible for us is {aufsicht}.

## What we do not do

No advertising, no tracking, no sharing with third parties, no analysis of text messages (the bot does not read messages in your channels), no voice profiles on the bot.

## Changes

If something about the bot changes, we update this text and note the date below. The current version is at {url}/discord-bot/datenschutz."""

_NB_DE = """## 1. Wer und was

Diese Bedingungen gelten zwischen dir und {name}, {anschrift_zeile}, {kontakt}, für die Nutzung des Discord-Bots „Taleward“. Der Bot nimmt Spielabende in Discord-Sprachkanälen auf und bringt die Tonspuren zum Taleward-Server eurer Gruppe. Für diesen Server gelten dessen eigene Bedingungen; für Discord gelten die Bedingungen von Discord.

## 2. Wer den Bot nutzen darf

Du musst mindestens 16 Jahre alt sein. Wer den Bot auf einen Discord-Server einlädt oder eine Kampagne verbindet, muss dort dazu berechtigt sein.

## 3. Aufnahmen nur mit Zustimmung

Der Bot nimmt nur Personen auf, die selbst zugestimmt haben, jede für sich. Das verhindert der Bot technisch; trotzdem gilt: Die Spielleitung sorgt dafür, dass alle im Kanal wissen, dass aufgenommen wird, und niemand zu einer Zustimmung gedrängt wird. Der Bot zeigt die Liste der aufgenommenen Personen während der ganzen Aufnahme im Kanal. Wer nicht aufgenommen werden will, tut einfach nichts.

## 4. Was du nicht darfst

- den Bot nutzen, um Personen ohne ihre Zustimmung aufzunehmen, etwa über ein zweites Gerät oder durch Umgehen der Zustimmung;
- Aufnahmen für etwas anderes verwenden, als die Gruppe vereinbart hat;
- den Bot stören, überlasten oder versuchen, an Daten anderer Personen zu kommen;
- gegen die Bedingungen von Discord verstoßen.

Bei Verstößen können wir die Verbindung zwischen Kampagne und Discord-Server lösen und den Bot von eurem Discord-Server nehmen.

## 5. Was der Bot kann und was nicht

Der Bot wird nach bestem Wissen gepflegt, aber ohne Gewähr. Eine Aufnahme kann ausfallen, Lücken haben oder nicht ankommen, etwa bei Störungen bei Discord oder beim Server eurer Gruppe. Verlasst euch bei wichtigen Runden nicht allein auf den Bot. Wir können den Bot ändern, pausieren oder einstellen; laufende Aufnahmen beenden wir dabei nicht absichtlich.

## 6. Haftung

Wir haften unbeschränkt bei Vorsatz und grober Fahrlässigkeit sowie nach dem Produkthaftungsgesetz und bei Verletzung von Leben, Körper oder Gesundheit. Bei einfacher Fahrlässigkeit haften wir nur für die Verletzung wesentlicher Pflichten, ohne die die Nutzung nicht möglich wäre, und nur bis zum typischen, vorhersehbaren Schaden. Der Bot ist für euch kostenlos; darüber hinaus haften wir nicht.

## 7. Kosten

Die Nutzung des Bots ist kostenlos. Ob der Taleward-Server eurer Gruppe für die Verarbeitung etwas berechnet, regelt dessen Betreiber; der Bot selbst berechnet nichts.

## 8. Datenschutz

Was der Bot mit Daten tut, steht in der Datenschutzerklärung unter {url}/discord-bot/datenschutz.

## 9. Änderungen und Recht

Wir können diese Bedingungen ändern; die gültige Fassung steht unter {url}/discord-bot/nutzungsbedingungen mit Datum. Es gilt deutsches Recht. Für Verbraucher gelten zusätzlich die zwingenden Vorschriften des Staates, in dem sie wohnen."""

_NB_EN = """## 1. Who and what

These terms apply between you and {name}, {anschrift_zeile}, {kontakt}, for the use of the Discord bot “Taleward”. The bot records game nights in Discord voice channels and delivers the audio tracks to your group’s Taleward server. That server has its own terms; Discord has its own terms.

## 2. Who may use the bot

You must be at least 16 years old. Whoever invites the bot to a Discord server or connects a campaign must be entitled to do so there.

## 3. Recording only with consent

The bot records only people who have consented themselves, each one for themselves. The bot enforces this technically; still, the game master makes sure everyone in the channel knows a recording is running and nobody is pushed into consenting. The bot shows the list of recorded people in the channel for the whole recording. If you do not want to be recorded, simply do nothing.

## 4. What you must not do

- use the bot to record people without their consent, for example through a second device or by circumventing consent;
- use recordings for anything other than what the group agreed;
- disrupt or overload the bot, or try to obtain other people’s data;
- violate Discord’s terms.

In case of violations we may disconnect the campaign from the Discord server and remove the bot from your Discord server.

## 5. What the bot can and cannot do

The bot is maintained to the best of our knowledge but without warranty. A recording may fail, have gaps or not arrive, for example during disruptions at Discord or at your group’s server. Do not rely on the bot alone for sessions that matter to you. We may change, pause or discontinue the bot; we do not deliberately end running recordings when doing so.

## 6. Liability

We are liable without limitation for intent and gross negligence, under the German Product Liability Act, and for injury to life, body or health. For slight negligence we are liable only for breach of essential obligations without which use would not be possible, and only up to the typical, foreseeable damage. The bot is free of charge for you; beyond that we are not liable.

## 7. Costs

Using the bot is free of charge. Whether your group’s Taleward server charges for processing is up to its operator; the bot itself charges nothing.

## 8. Privacy

What the bot does with data is described in the privacy policy at {url}/discord-bot/datenschutz.

## 9. Changes and law

We may change these terms; the current version is at {url}/discord-bot/nutzungsbedingungen with its date. German law applies. For consumers, the mandatory provisions of the country they live in apply in addition."""
