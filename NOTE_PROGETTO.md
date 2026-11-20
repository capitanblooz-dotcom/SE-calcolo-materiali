# SE-script – calcolo materiali per blueprint di Space Engineers 1

Note per riprendere il lavoro (es. in VS Code). Stato al 20/11/2026.

## Obiettivo
Dato il file XML di una nave (`bp.sbc`), calcolare blocchi → componenti → lingotti → minerali.

## File
- `se_gui.py` / `se_gui.pyw` – **interfaccia grafica** (tkinter): scelta Vanilla o mondo salvato, scelta nave,
  parametri, schede Riepilogo / Blocchi / Componenti / Lingotti / Minerali / Avvisi / Log, export CSV.
  Ricorda le ultime scelte in `se_gui_settings.json`.
- `se_calc.py` – motore di calcolo + CLI (Python 3, solo libreria standard).
- `bp.sbc` – blueprint di test: "miniminer (capitanblooz)", 1 griglia small, 32 blocchi.
- Le blueprint del gioco stanno in `%AppData%\SpaceEngineers\Blueprints\local\<nome>\bp.sbc`.

## Uso
```
python se_gui.py                                  # interfaccia grafica
python se_calc.py bp.sbc --vanilla                # solo gioco base, nessuna mod
python se_calc.py bp.sbc --world "%AppData%\SpaceEngineers\Saves\<id>\<mondo>"
python se_calc.py bp.sbc --blocks
python se_calc.py bp.sbc --assembler 3 --csv risultato.csv
python se_calc.py bp.sbc --mod 2877712858        # carica una mod per intero (sovrascrive le ricette)
python se_calc.py nave1\bp.sbc nave2\bp.sbc --count 2
```
Opzioni: `--game` (default cerca D:\SteamLibrary\...\SpaceEngineers), `--mods-dir`
(default D:\SteamLibrary\steamapps\workshop\content\244850), `--no-auto-mods`,
`--assembler` (efficienza mondo 1/3/10), `--refinery-yield` (1 = raffineria, 0.7 = altoforno;
>1 per i moduli resa), `--count`, `--blocks`, `--csv` (separatore `;`, per Excel IT).

## Dove sono i dati del gioco
- Vanilla: `D:\SteamLibrary\steamapps\common\SpaceEngineers\Content\Data`
  - `CubeBlocks\*.sbc` → `<Definition>` con `<Id>` (TypeId/SubtypeId) e `<Components><Component Subtype Count/>`
    (lo stesso Subtype può comparire più volte → vanno sommati).
  - `Blueprints.sbc` → `<Blueprint>` con `<Prerequisites><Item Amount TypeId SubtypeId/>` e `<Result .../>`
    oppure `<Results><Item/></Results>`. Es.: SteelPlate = 21 Iron ingot; IronOreToIngot 1 ore → 0,7 ingot.
  - Le cartelle `Blueprints`, `Prefabs`, `Missions`… contengono navi, non definizioni: vanno saltate.
  - Raffineria: `MaterialEfficiency` 1.0; Basic refinery 0.7 (CubeBlocks_Production.sbc).
- Mod workshop: `D:\SteamLibrary\steamapps\workshop\content\244850\<id>\Data\*.sbc` (~400 mod, ~1700 file).

## Come funziona lo script (scelte fatte)
1. Legge la blueprint: per ogni `CubeGrid` prende solo i **figli diretti** di `<CubeBlocks>`
   (altrimenti conterebbe anche gli oggetti negli inventari, es. Deuterium nel cargo).
   Tipo = attributo `xsi:type` senza `MyObjectBuilder_` (se assente = `CubeBlock`, cioè armature);
   sottotipo = `<SubtypeName>` (può essere vuoto).
2. Carica tutte le definizioni vanilla.
3. Per i blocchi non trovati cerca nelle mod (ricerca testuale veloce di `<SubtypeId>X</SubtypeId>`)
   e carica quelle mod in modo **secondario**: aggiungono solo blocchi mancanti e ricette per oggetti
   che non hanno già una ricetta. Motivo: alcune mod (es. 2877712858 "Core…VanillaOre_Override")
   cambiano le rese dei minerali e falsavano tutto (oro ×10). Se il mondo usa davvero quella mod,
   passarla con `--mod` (caricamento completo con override).
4. Scelta ricetta (`recipe_for`): priorità a vanilla/esplicite, poi risultato singolo, Id = sottotipo,
   IsPrimary. Per i lingotti si escludono le ricette da Scrap.
5. Espansione ricorsiva: componenti → (componenti) → lingotti → minerali.
   Lingotti ÷ efficienza assemblatore; minerali = lingotti ÷ (resa ricetta × refinery-yield).
6. Segnala blocchi senza definizione e materiali senza ricetta.

## Risultati sul bp.sbc di test
- Blocchi da mod: `SmallBlockDrill2x`, `SmallBlockMediumContainer2x`, `SmallBlockOreDetector2x`
  (mod "2x", presenti in due versioni: 1682499145 e 2785976305 – viene usata la prima trovata),
  componente di mod `Tech2x`.
- `GanymedeIncineratorSmall` → mod 2413537313 / 2564086804 / 2570814389.
- **Non trovati in nessuna mod installata**: `GanymedeSmallBlockSmallThrust` (14) e
  `GanymedeSmallBlockBatteryBlock` (2) → non conteggiati (mod Ganymede non installata/rimossa).
- Totale minerali (senza quei 16 blocchi): Gold ~83.200 kg, Iron ~27.241, Silver 12.480,
  Silicon ~6.692, Cobalt ~5.547, Nickel ~1.088 (assemblatore ×1, raffineria ×1).

## Novita' (20/11/2026, interfaccia)
- **Mondo**: legge `Sandbox_config.sbc` (o `Sandbox.sbc`) → `AssemblerEfficiencyMultiplier` e lista `<Mods>`.
  Le mod del mondo vengono caricate **complete** (override), dal basso verso l'alto della lista
  (nel gioco la mod piu' in alto ha la priorita'). Mod locali cercate in `%AppData%\SpaceEngineers\Mods`.
- **Vanilla**: nessun mondo, nessuna mod, nessuna ricerca automatica (`--vanilla` da CLI).
- Mod "legacy" (`*_legacy.bin`, zip con `Data/*.sbc`) ora lette (113 mod su 400 sono cosi').
- Calcolo per unita': per ogni componente i lingotti per pezzo e totali; per ogni lingotto il minerale
  per kg e totale; minerali usati direttamente dall'assemblatore indicati a parte.
- Nomi leggibili in italiano da `Localization\MyTexts.it.resx` (fallback inglese / sottotipo).

## Server online e mondo di test (20/11/2026)
- Il client NON salva i mondi dei server dedicati (in `Saves\...\<server>` c'e' solo `Storage` delle mod).
- `find_servers_from_logs()`: legge `%AppData%\SpaceEngineers\SpaceEngineers_*.log`. Sequenza:
  `Joining server: Name:X ConnectionString:Y` → `World requested` → `List of used mods (N) - START`
  ... `Id = Steam:ID, Filename = 'ID.sbm', Name = '...'` ... `List of used mods - END`.
  Se in mezzo c'e' `Loading Sandbox world configuration file` / `Loading available saves` era un mondo
  locale e si scarta. Vale l'ultimo accesso riuscito. Le impostazioni del server NON sono nei log.
- GUI scheda Calcolo: modalita' "Server online" (solo calcolo, mod del server, assemblatore manuale).
- GUI scheda "Mondo di test locale": `create_test_world()` copia `Content\CustomWorlds\<modello>`
  (default Empty World) in `Saves\<SteamID>\<nome>`, imposta SessionName, WorldId nuovo, LastSaveTime,
  `<Settings>` (valori predefiniti letti dal modello = vanilla) e `<Mods>` in Sandbox.sbc e
  Sandbox_config.sbc; toglie i `*.sbcB5`.
  **Sandbox_config.sbc deve avere la struttura dei salvataggi veri: Settings, Mods, SessionName,
  LastSaveTime** (il gioco lo usa per sovrascrivere il checkpoint: senza SessionName il mondo non
  compare in "Carica partita" – bug trovato e corretto il 20/11/2026).
  **Il nodo radice deve dichiarare `xmlns:xsd`**: i file usano `xsi:type="xsd:long"` nei valori ed
  ElementTree toglie la dichiarazione → il gioco non legge il mondo e lo scarta senza errori nel log
  (`ensure_xml_namespaces()`).
  **Non copiare i `*.scf`** dei modelli (Star System e altri): sono descrittori di campagna e la
  cartella non comparirebbe in "Carica partita" (il gioco la tratta come campagna).
  Mai sovrascrivere: se la cartella esiste → errore.
- **Giocatore/tuta** (`find_local_player`, `add_player_character`): un salvataggio copiato da un modello
  non ha il giocatore locale → schermata di respawn senza opzioni. I salvataggi recenti NON contengono lo
  SteamID: il giocatore e' `ClientId 0` + `HashedId` (per capitanblooz: 9851313350798392788, uguale in tutti
  i mondi). Si legge dai 25 salvataggi piu' recenti (primo elemento di AllPlayersData).
  Si scrivono: Identity (CharacterEntityId), AllPlayersData (Key → IdentityId), ControlledEntities
  (personaggio → giocatore), ControlledObject/CameraEntity. Se il modello ha un personaggio (Empty World,
  Red Ship...) lo si intesta al giocatore; se no (Star System, Home System, Distant Moons) se ne copia uno
  da Empty World nella SpectatorPosition del modello (Star System: ~174 km sopra la Terra). Se il gioco
  ha gia' creato il giocatore (mondo aperto una volta) si riusa la sua identita'. Si cancella
  `SANDBOX_0_0_0_.sbsB5` perche' il settore e' modificato.
- **AppVersion**: i modelli sono a 1166003; il gioco (01_211_018 → 1211018) avvisa "formato obsoleto,
  verra' aggiornato al primo salvataggio". `current_game_version()` legge la versione dall'intestazione
  del log (`App Version: 01_211_018`) o dai salvataggi recenti e la scrive nel nuovo Sandbox.sbc.
  Le altre differenze col mondo salvato dal gioco (118 impostazioni nuove, fazioni NPC, cache Vicinity...)
  le aggiunge il gioco da solo al primo caricamento.
- Verificato in gioco (20/11/2026): mondo Star System caricato con le mod del server, partenza in tuta.
- **Avviso "formato obsoleto / verra' aggiornato al salvataggio"**: NON dipende da AppVersion. Nel log e'
  `Incorrect save data` + `MyLoadingNeedXMLException`: il gioco carica il settore dal binario
  `SANDBOX_0_0_0_.sbsB5`; se manca (lo cancelliamo quando modifichiamo lo .sbs) chiede di ricaricare da XML.
  Verificato: CLONE/CLONE3/CLONE4 con B5 originale del modello → nessun avviso; dopo l'aggiunta del
  personaggio (B5 cancellato) → avviso. Non sappiamo scrivere il B5 (formato binario interno).
  Soluzione: se il modello ha gia' il personaggio (10 modelli su 13, es. Empty World, Pertam Orbiter,
  Dead Drop Arena) non si tocca il settore e si cambia solo Sandbox.sbc → nessun avviso.
  Star System / Home System / Distant Moons non hanno personaggio → va aggiunto al settore → avviso una
  sola volta, innocuo (dopo il primo salvataggio il gioco ha riscritto il B5).
  Da verificare in gioco: che con il settore invariato il personaggio del modello venga dato al giocatore.

## Idee / TODO
- Scegliere quale versione della mod "2x" usare (oggi: la prima in ordine numerico) o leggere
  la lista mod da un salvataggio (`Sandbox.sbc` → `<Mods>`) per caricare esattamente le mod del mondo.
- Tabella manuale (JSON) per blocchi sconosciuti (es. Ganymede) da integrare a mano.
- Localizzazione dei nomi definiti nelle mod (oggi solo testi del gioco base).
- Valore esatto del bonus dei moduli resa raffineria (da verificare nei file del gioco).
- Calcolo tempi di produzione (`BaseProductionTimeInSeconds`) e massa/volume.
- Nota tecnica: in sessione Cowork il gioco era montato come `~/mnt/Data` e le mod come `~/mnt/244850`
  (quindi si usava `--game ~/mnt/Data --mods-dir ~/mnt/244850`); su Windows bastano i default.
