# BIR-kalender

BIR-kalender hentar tømmedagar frå BIR og lagar ei ICS-fil som kan abonnerast
på i Google Kalender, Apple Kalender og andre kalenderappar. Du kan køyre
scriptet lokalt eller la GitHub Actions oppdatere kalenderen automatisk kvar
måndag.

## Abonner

Kvar kalender får ei eiga abonnementsadresse:

```text
https://esnalabu.github.io/bir-calendar/<CALENDAR_PATH>
```

`<CALENDAR_PATH>` er den tilfeldige stien du lagrar som secret for kalenderen.
Han er stabil så lenge du ikkje endrar han.

## Kva du treng

- Python 3.12 eller nyare
- [uv](https://docs.astral.sh/uv/)
- Adressa til eigedommen du vil hente tømmedagar for
- Du kan også oppgi BIR sin eigedoms-ID (`rId`), og det gjer oppslaget raskare
  og meir stabilt
- Ein tilfeldig og stabil `calendar_id`

## Snøggstart

```bash
git clone https://github.com/esnalabu/bir-calendar.git
cd bir-calendar
uv sync
cp calendars.example.toml calendars.toml
```

Opne `calendars.toml` og fyll inn adresse, valfri `r_id` og ein tilfeldig
`calendar_id`. Du kan lage ein `calendar_id` slik:

```bash
python -c "import uuid; print(uuid.uuid4())"
```

Generer kalenderen:

```bash
uv run python scripts/update_calendar.py --output dist/bir-kalender.ics
```

Importer `dist/bir-kalender.ics` i kalenderappen, eller publiser fila til ein
stad appen kan abonnere på.

## Konfigurasjon

`calendars.toml` er gitignorert og skal ikkje committast. Filformatet er:

```toml
months = 3

[[calendars]]
address = "Adresse 1, 5000 Bergen"
r_id = "00000000-0000-0000-0000-000000000000"
calendar_id = "00000000-0000-0000-0000-000000000000"
output = "bir-kalender.ics"
# categories = ["restavfall", "papir", "glass"]
```

Felta har denne tydinga:

- `address` er påkravd og skal vere adressa slik ho blir skriven i BIR sitt
  adressesøk.
- `r_id` er valfri. Viss ho manglar, blir eigedoms-ID-en slått opp via adressa.
- `calendar_id` er påkravd og bør vere ein tilfeldig UUID som du held stabil.
  Verdien blir hasha før bruk i UID-ane i ICS-fila.
- `output` er valfri og er filnamnet i output-mappa. Standard er
  `calendar-1.ics`.
- `categories` er valfri og er ei liste over kategoriar som skal inkluderast.
  Viss ho manglar, blir alle kategoriar med. Gyldige verdiar er `restavfall`,
  `matavfall`, `papir`, `plast` og `glass`. `papir` dekkjer «papir og
  plastemballasje».
- `months` er valfri og er talet på kalendermånader som skal genererast.
  Standard er 3.

Du kan ha fleire `[[calendars]]`-oppføringar i same fil. Kvar oppføring blir
skriven til si eiga fil med mindre du brukar `--output`, som berre kan brukast
med éin kalender. I GitHub Actions blir kvar kalender konfigurert som eit eige
environment i staden for i `calendars.toml`.

## Kommandolinje

```bash
uv run python scripts/update_calendar.py [val]
```

- `--config STI` vel ei anna config-fil enn `calendars.toml`.
- `--output STI` skriv til ein bestemt fil. Dette kan berre brukast med éin
  kalender.
- `--output-dir STI` vel mappa som filene blir skrivne til. Standard er
  `dist`.
- `--months TAL` overstyrer `months` frå config-fila.
- `--min-events TAL` krev eit minste tal tømmedagar før fila blir skriven.
  Standard er 1.

## Automatisk oppdatering med GitHub Actions

Workflowen `.github/workflows/update-calendar.yml` oppdaterer alle kalendrane i
matrix-lista kvar måndag og publiserer dei samla til GitHub Pages.

1. Legg koden i eit GitHub-repo.
2. Set GitHub Pages-kjelda til «GitHub Actions» under `Settings > Pages`.
3. Lag eit GitHub Environment for kvar kalender, til dømes `default`.
4. Legg desse environment-secrets inn:

   - `BIR_ADDRESS`: adressa som skal brukast i BIR sitt adressesøk
   - `BIR_RID`: valfri eigedoms-ID. Viss `BIR_RID` ikkje er sett, blir
     eigedoms-ID-en slått opp via adressa.
   - `BIR_CALENDAR_ID`: tilfeldig og stabil ID, til dømes ein UUID
   - `CALENDAR_PATH`: tilfeldig og stabil sti, til dømes 32 hex-teikn + `.ics`

   Legg eventuelt også inn environment-variabelen `BIR_CATEGORIES` (ikkje
   secret) med ei kommaseparert liste, til dømes `restavfall,papir,glass`.
   Viss han ikkje er sett, blir alle kategoriar inkluderte. Dersom du ikkje
   leverer matavfall er `restavfall,papir,glass` rett liste.

5. Lag `CALENDAR_PATH` med:

   ```bash
   python -c "import secrets; print(secrets.token_hex(16) + '.ics')"
   ```

   Lagre verdien som secret og ikkje endre han, fordi då endrar
   abonnementsadressa seg.

6. Legg environment-namnet inn i matrix-lista `calendar` i workflowen.
7. Køyr workflowen `Oppdater BIR-kalender` manuelt éin gong.

Kvar oppføring i matrix-lista blir ein eigen jobb. Du legg til fleire kalendrar
ved å lage eit nytt environment, leggje inn secrets og leggje namnet til i
matrix-lista.

Workflowen feilar med ei forklaring dersom `BIR_ADDRESS`, `BIR_CALENDAR_ID`
eller `CALENDAR_PATH` manglar for eit environment. Han feilar også dersom
`CALENDAR_PATH` har ugyldig format eller ikkje endar med `.ics`.

Kvar jobb skriv til `_site/<CALENDAR_PATH>`. Deploy-jobben slår saman artifacta
og publiserer dei på GitHub Pages. Dersom ein kalenderjobb feilar, blir ikkje
deploy-jobben køyrd, og førre Pages-versjon blir liggjande.

## Personvern

Kalenderen inneheld berre generiske hendingar som «BIR: Restavfall». Han
inneheld ikkje adresse, `rId`, `LOCATION`, `URL` eller lenkjer til BIR sine
adressesider. `calendar_id` blir hasha før bruk i UID-ane.

Adresse og `rId` skal ikkje liggje i kildekoden. I GitHub Actions blir dei
henta frå environment-secrets, og lokalt ligg dei i den gitignorerte
`calendars.toml`. Dersom du publiserer ICS-fila via GitHub Pages, er ho
offentleg. `CALENDAR_PATH` gjer URL-en vanskeleg å gjette, men han er ikkje
tilgangskontroll. Mønsteret med tømmedagar kan også gjere kalenderen mogleg å
knyte til ein bestemt eigedom. Bruk annan hosting med tilgangskontroll dersom
kalenderen må vere privat.

## Feilsøking

- BIR-API-et gir berre eksakte datoar for inneverande månad. Månad 2 og 3 er
  projiserte ut frå repetisjonen og kan bli feil ved ruteendringar.
- Viss kalenderen blir tom, bør du kontrollere `address` og `r_id`. Du kan også
  prøve `--min-events 1` for å sjå om det blir generert nokon hendingar.
- Viss kategoriar manglar, kontroller `categories` i `calendars.toml` eller
  `BIR_CATEGORIES` i GitHub Actions. `--min-events` tel hendingar etter
  filtrering.
- Viss CI seier at `CALENDAR_PATH` manglar, legg til eit environment-secret med
  eit tilfeldig filnamn som endar med `.ics`.
- Viss CI seier at `CALENDAR_PATH` har ugyldig format, bruk berre bokstavar,
  tal, punktum, bindestrek, understrek og skråstrek, utan `..` eller leiande
  `/`.
- Adressesida og tømmekalenderen til BIR kan endre HTML-struktur. Då kan
  parseren i `scripts/update_calendar.py` trenge ei oppdatering.
- Køyr testane med `uv run pytest -q` etter endringar.

## Kjelder og attribusjon

API-detaljane er undersøkte med hjelp av
[pybirno](https://github.com/esnalabu/pybirno), MIT-lisensiert,
© 2026 Sander Blom. Prosjektet brukar ikkje pybirno som avhengigheit og
kopierer ikkje kode frå det.

BIR-API-et er ikkje eit offisielt, dokumentert API for tredjepart. Tenesta kan
endre seg eller slutte å fungere utan varsel. Bruk ho med forsiktig frekvens og
respekter BIR sine vilkår.
