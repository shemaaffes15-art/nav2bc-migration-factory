"""Migration plan: the ordered steps that take a NAV database to Business Central (Online).

The plan follows the upgrade path computed by ``upgrade_path.compute_path``. Each step is:

* ``powershell`` — a script generated from the project environment, shown to the consultant,
  editable, and run only after a nominative validation;
* ``internal`` — work done by the application itself (inventory, compile-log reading, row-count
  comparison);
* ``manual`` — an action done outside the application (VS Code, BC Online admin center), ticked
  off by the consultant.

Generated scripts are *templates built from the Microsoft documentation*: they must be read
before running. A script dropped in ``<workdir>\\scripts\\<step id>.ps1`` replaces the generated
one (this is where the team's own PowerShell scripts plug in).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..upgrade_path import compute_path

DOC = "https://learn.microsoft.com/dynamics365/business-central/dev-itpro"
DOC_PATHS = f"{DOC}/upgrade/upgrade-paths"
DOC_V14 = f"{DOC}/upgrade/upgrading-to-business-central-on-premises"
DOC_APP_CODE = f"{DOC}/upgrade/upgrading-the-application-code"
DOC_V14_CUSTOM = f"{DOC}/upgrade/upgrade-to-microsoft-base-app-v22"
DOC_DEST_APPS = f"{DOC}/upgrade/upgrade-destinationappsformigration"
DOC_TXT2AL = f"{DOC}/developer/devenv-txt2al-tool"
DOC_MIGRATE = f"{DOC}/administration/migrate-nav"
DOC_CLOUD = f"{DOC}/administration/cloud-migration-setup"

PHASES = {
    "prep": "Préparation",
    "nav2013": "NAV 2009 → NAV 2013",
    "bc14": "NAV → BC 14 (C/AL)",
    "al": "Conversion C/AL → AL",
    "bc25": "BC 14 → BC 25",
    "target": "Version cible",
    "cloud": "Migration vers BC Online",
    "validation": "Validation",
}


@dataclass
class Step:
    id: str
    phase: str
    title: str
    kind: str  # powershell | internal | manual
    description: str
    documentation_url: str | None = None
    destructive: bool = False
    requires: list[str] = field(default_factory=list)  # environment keys
    produces: list[str] = field(default_factory=list)
    script: str | None = None
    checklist: list[str] = field(default_factory=list)
    evidence: str = "documented"  # documented | to_verify | factory

    def to_dict(self) -> dict:
        d = asdict(self)
        d["phase_label"] = PHASES[self.phase]
        return d


# --------------------------------------------------------------------------------------------
# PowerShell helpers
# --------------------------------------------------------------------------------------------

def q(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _header(env: dict, step_id: str, extra: dict[str, str] | None = None) -> str:
    lines = [
        f"# NAV2BC Migration Factory — étape {step_id}",
        "# Script généré à partir de la documentation Microsoft : à relire avant exécution.",
        "$ErrorActionPreference = 'Stop'",
        f"$Work = {q(env['workdir'])}",
        "New-Item -ItemType Directory -Force -Path $Work | Out-Null",
    ]
    for k, v in (extra or {}).items():
        lines.append(f"${k} = {q(v)}")
    return "\n".join(lines) + "\n"


SQL_FN = r"""
function Invoke-Sql([string]$Server, [string]$Query, [string]$Database = 'master') {
    $cn = New-Object System.Data.SqlClient.SqlConnection("Server=$Server;Database=$Database;Integrated Security=True")
    $cn.Open()
    try {
        $cmd = $cn.CreateCommand(); $cmd.CommandText = $Query; $cmd.CommandTimeout = 0
        $da = New-Object System.Data.SqlClient.SqlDataAdapter($cmd)
        $dt = New-Object System.Data.DataTable; [void]$da.Fill($dt)
        return ,$dt
    } finally { $cn.Close() }
}
"""

FINSQL_FN = r"""
function Invoke-Finsql([string]$Exe, [string]$Arguments, [string]$LogFile) {
    if (-not (Test-Path $Exe)) { throw "finsql.exe introuvable : $Exe" }
    if (Test-Path $LogFile) { Remove-Item $LogFile -Force }
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -Wait -PassThru -NoNewWindow
    if (Test-Path $LogFile) {
        $log = Get-Content $LogFile -Raw
        if ($log -and $log.Trim()) { Write-Output $log; throw "finsql a signalé des erreurs (voir ci-dessus)." }
    }
    Write-Output "finsql terminé (code $($p.ExitCode))."
}
"""

ROWCOUNT_SQL = ("SELECT t.name AS TableName, SUM(p.rows) AS [Rows] FROM sys.tables t "
                "JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1) "
                "GROUP BY t.name ORDER BY t.name")


def _backup_script(env: dict, step_id: str, database_key: str, suffix: str) -> str:
    return _header(env, step_id, {"Server": env["sql_server"], "Database": env[database_key],
                                  "BackupDir": env["backup_dir"]}) + SQL_FN + f"""
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$File = Join-Path $BackupDir ("$Database" + '_{suffix}_' + (Get-Date -Format 'yyyyMMdd_HHmm') + '.bak')
Write-Output "Sauvegarde de [$Database] vers $File"
Invoke-Sql $Server "BACKUP DATABASE [$Database] TO DISK = N'$File' WITH COPY_ONLY, INIT, CHECKSUM, STATS = 10" | Out-Null
Set-Content -Path (Join-Path $Work 'last_backup_{database_key}.txt') -Value $File
Write-Output "Sauvegarde terminée : $File"
"""


# --------------------------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------------------------

def _prep(env: dict) -> list[Step]:
    export_file = env["workdir"] + r"\export\nav_all_objects.txt"
    return [
        Step("prep.backup", "prep", "Sauvegarder la base NAV d'origine", "powershell",
             "Sauvegarde COPY_ONLY de la base NAV. La base d'origine n'est plus jamais touchée ensuite.",
             requires=["sql_server", "nav_database", "backup_dir"],
             script=_backup_script(env, "prep.backup", "nav_database", "source")),
        Step("prep.export", "prep", "Exporter tous les objets NAV (TXT)", "powershell",
             "Export de tous les objets avec le Development Environment de la version source. "
             "Le fichier alimente automatiquement l'inventaire, l'analyse des rapports et la fusion.",
             documentation_url=DOC_APP_CODE, requires=["nav_finsql", "sql_server", "nav_database"],
             produces=[export_file],
             script=_header(env, "prep.export", {"Finsql": env["nav_finsql"], "Server": env["sql_server"],
                                                 "Database": env["nav_database"]}) + FINSQL_FN + r"""
$Out = Join-Path $Work 'export\nav_all_objects.txt'
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
$Log = Join-Path $Work 'export\finsql_export.log'
Invoke-Finsql $Finsql "command=exportobjects, file=`"$Out`", servername=$Server, database=`"$Database`", ntauthentication=yes, logfile=`"$Log`"" $Log
Write-Output ("Export : {0:N0} Ko" -f ((Get-Item $Out).Length / 1KB))
"""),
        Step("prep.inventory", "prep", "Inventaire et analyse des rapports", "internal",
             "Lit l'export TXT : objets par type, objets client, champs ajoutés aux tables standard, "
             "fonctions indisponibles en BC Online, analyse de chaque rapport.",
             produces=[export_file]),
        Step("prep.restore", "prep", "Créer la base de travail (copie)", "powershell",
             "Restaure la dernière sauvegarde sous le nom de la base de travail. Toutes les mises à niveau "
             "s'appliquent à cette copie. Le script s'arrête si la base de travail existe déjà.",
             destructive=True, requires=["sql_server", "work_database", "backup_dir"],
             script=_header(env, "prep.restore", {"Server": env["sql_server"], "Target": env["work_database"]})
             + SQL_FN + r"""
$Bak = Get-Content (Join-Path $Work 'last_backup_nav_database.txt') -ErrorAction Stop
$exists = Invoke-Sql $Server "SELECT name FROM sys.databases WHERE name = N'$Target'"
if ($exists.Rows.Count -gt 0) { throw "La base [$Target] existe déjà. Supprimez-la vous-même ou changez le nom dans l'environnement." }
$dataDir = (Invoke-Sql $Server "SELECT CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(400)) AS P").Rows[0].P
$logDir = (Invoke-Sql $Server "SELECT CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(400)) AS P").Rows[0].P
$files = Invoke-Sql $Server "RESTORE FILELISTONLY FROM DISK = N'$Bak'"
$moves = @(); $i = 0
foreach ($f in $files.Rows) {
    $dir = if ($f.Type -eq 'L') { $logDir } else { $dataDir }
    $ext = if ($f.Type -eq 'L') { '.ldf' } else { '.mdf' }
    if ($i -gt 0 -and $f.Type -ne 'L') { $ext = "_$i.ndf" }
    $moves += "MOVE N'$($f.LogicalName)' TO N'$(Join-Path $dir ($Target + $ext))'"; $i++
}
$sql = "RESTORE DATABASE [$Target] FROM DISK = N'$Bak' WITH " + ($moves -join ', ') + ", STATS = 10"
Write-Output $sql
Invoke-Sql $Server $sql | Out-Null
Write-Output "Base de travail [$Target] créée depuis $Bak"
"""),
    ]


def _nav2013() -> list[Step]:
    return [Step("nav2013.upgrade", "nav2013", "Mise à niveau intermédiaire vers NAV 2013", "manual",
                 "Source NAV 2009 : étape intermédiaire à confirmer avec la documentation Microsoft avant "
                 "de planifier (conversion de la base et du code Classic, rapports Classic à reconstruire).",
                 evidence="to_verify",
                 checklist=["Chemin NAV 2009 → NAV 2013 confirmé dans la documentation",
                            "Base convertie en NAV 2013 et objets compilés",
                            "Rapports Classic recensés (écran Rapports)"])]


def _bc14(env: dict) -> list[Step]:
    adm, inst = env["bc14_admin"], env["bc14_instance"]
    common = {"Admin": adm, "Instance": inst, "Server": env["sql_server"], "Database": env["work_database"]}
    return [
        Step("bc14.merge", "bc14", "Fusionner le code client avec le standard BC14", "powershell",
             "Merge-NAVApplicationObject : Original (standard NAV d'origine) + Modified (base client) + "
             "Target (standard BC14) → Result. Les conflits sont listés et à résoudre avant l'import.",
             documentation_url=DOC_APP_CODE,
             requires=["bc14_model_tools", "original_std_txt", "target_std_txt"],
             produces=[env["workdir"] + r"\merge\result"],
             script=_header(env, "bc14.merge", {"ModelTools": env["bc14_model_tools"],
                                                "Original": env["original_std_txt"],
                                                "Target": env["target_std_txt"]}) + r"""
Import-Module $ModelTools -Force
$Modified = Join-Path $Work 'export\nav_all_objects.txt'
$Result = Join-Path $Work 'merge\result'
New-Item -ItemType Directory -Force -Path $Result | Out-Null
$r = Merge-NAVApplicationObject -OriginalPath $Original -ModifiedPath $Modified -TargetPath $Target -ResultPath $Result -Force
$r.Summary | Format-List | Out-String | Write-Output
$conflicts = @($r | Where-Object { $_.MergeResult -eq 'Conflict' })
Write-Output "Objets en conflit : $($conflicts.Count)"
$conflicts | ForEach-Object { Write-Output ("CONFLIT {0} {1}" -f $_.ObjectType, $_.Id) }
"""),
        Step("bc14.conflicts", "bc14", "Résoudre les conflits de fusion", "manual",
             "Dans le dossier merge\\result, corriger chaque objet signalé (fichiers .CONFLICT), puis supprimer "
             "les fichiers .CONFLICT traités.",
             documentation_url=DOC_APP_CODE,
             checklist=["Tous les conflits résolus", "Objets client relus (fonctions SaaS signalées dans l'inventaire)"]),
        Step("bc14.join", "bc14", "Assembler le fichier fusionné", "powershell",
             "Join-NAVApplicationObjectFile : un seul TXT prêt à importer dans BC14.",
             requires=["bc14_model_tools"], produces=[env["workdir"] + r"\merge\merged.txt"],
             script=_header(env, "bc14.join", {"ModelTools": env["bc14_model_tools"]}) + r"""
Import-Module $ModelTools -Force
$Result = Join-Path $Work 'merge\result'
if (Get-ChildItem $Result -Filter *.CONFLICT -ErrorAction SilentlyContinue) { throw 'Il reste des fichiers .CONFLICT dans merge\result.' }
Join-NAVApplicationObjectFile -Source (Join-Path $Result '*.TXT') -Destination (Join-Path $Work 'merge\merged.txt') -Force
Write-Output 'merged.txt créé.'
"""),
        Step("bc14.convert", "bc14", "Conversion technique de la base vers BC14", "powershell",
             "Invoke-NAVApplicationDatabaseConversion sur la base de travail (outil d'administration BC14).",
             documentation_url=DOC_V14, destructive=True,
             requires=["bc14_admin", "sql_server", "work_database"],
             script=_header(env, "bc14.convert", common) + r"""
Import-Module $Admin -Force
Invoke-NAVApplicationDatabaseConversion -DatabaseServer $Server -DatabaseName $Database -Force
Write-Output "Base [$Database] convertie au format BC14."
"""),
        Step("bc14.service", "bc14", "Raccorder le service BC14 à la base", "powershell",
             "Paramètre DatabaseServer / DatabaseName de l'instance, import de la licence, redémarrage.",
             documentation_url=DOC_V14, requires=["bc14_admin", "bc14_instance", "work_database"],
             script=_header(env, "bc14.service", {**common, "License": env["bc14_license"]}) + r"""
Import-Module $Admin -Force
Set-NAVServerConfiguration -ServerInstance $Instance -KeyName DatabaseServer -KeyValue $Server
Set-NAVServerConfiguration -ServerInstance $Instance -KeyName DatabaseName -KeyValue $Database
Restart-NAVServerInstance -ServerInstance $Instance
if ($License) { Import-NAVServerLicense -ServerInstance $Instance -LicenseFile $License; Restart-NAVServerInstance -ServerInstance $Instance }
Get-NAVServerInstance -ServerInstance $Instance | Format-List | Out-String | Write-Output
"""),
        Step("bc14.import", "bc14", "Importer et compiler les objets fusionnés", "powershell",
             "Import de merged.txt dans la base de travail puis compilation de tous les objets "
             "(synchronisation du schéma à l'étape suivante).",
             documentation_url=DOC_V14, destructive=True,
             requires=["bc14_finsql", "sql_server", "work_database"],
             script=_header(env, "bc14.import", {"Finsql": env["bc14_finsql"], "Server": env["sql_server"],
                                                 "Database": env["work_database"]}) + FINSQL_FN + r"""
$File = Join-Path $Work 'merge\merged.txt'
$Log = Join-Path $Work 'merge\finsql_import.log'
Invoke-Finsql $Finsql "command=importobjects, file=`"$File`", servername=$Server, database=`"$Database`", ntauthentication=yes, importaction=overwrite, synchronizeschemachanges=no, logfile=`"$Log`"" $Log
$Log2 = Join-Path $Work 'merge\finsql_compile.log'
Invoke-Finsql $Finsql "command=compileobjects, servername=$Server, database=`"$Database`", ntauthentication=yes, synchronizeschemachanges=no, logfile=`"$Log2`"" $Log2
"""),
        Step("bc14.sync", "bc14", "Synchroniser le schéma", "powershell",
             "Sync-NAVTenant -Mode Sync sur l'instance BC14.", documentation_url=DOC_V14, destructive=True,
             requires=["bc14_admin", "bc14_instance"],
             script=_header(env, "bc14.sync", common) + r"""
Import-Module $Admin -Force
Sync-NAVTenant -ServerInstance $Instance -Mode Sync -Force
Write-Output 'Schéma synchronisé.'
"""),
        Step("bc14.dataupgrade", "bc14", "Mise à niveau des données BC14", "powershell",
             "Start-NAVDataUpgrade (mode série) puis suivi jusqu'à la fin.", documentation_url=DOC_V14,
             destructive=True, requires=["bc14_admin", "bc14_instance"],
             script=_header(env, "bc14.dataupgrade", common) + r"""
Import-Module $Admin -Force
Start-NAVDataUpgrade -ServerInstance $Instance -FunctionExecutionMode Serial -Force
do {
    Start-Sleep -Seconds 15
    $s = Get-NAVDataUpgrade -ServerInstance $Instance
    Write-Output ("{0:HH:mm:ss}  {1}  {2}" -f (Get-Date), $s.State, $s.Progress)
} while ($s.State -eq 'InProgress')
Get-NAVDataUpgrade -ServerInstance $Instance -ErrorOnly | Format-List | Out-String | Write-Output
if ($s.State -ne 'Completed') { throw "Mise à niveau des données : état $($s.State)" }
"""),
        Step("bc14.check", "bc14", "Contrôler BC14", "manual",
             "Ouvrir le client BC14 sur la base de travail et vérifier les points clés.",
             checklist=["Connexion au client BC14", "Sociétés présentes", "Écritures et soldes cohérents",
                        "Comptages source / BC14 lancés (phase Validation)"]),
    ]


def _al(env: dict) -> list[Step]:
    return [
        Step("al.export", "al", "Exporter le code client au nouveau format", "powershell",
             "finsql command=exporttonewsyntax sur les objets modifiés, puis découpage objet par objet "
             "(Split-NAVApplicationObjectFile) pour Txt2AL. Le filtre se règle dans le script.",
             documentation_url=DOC_TXT2AL, requires=["bc14_finsql", "bc14_model_tools", "work_database"],
             produces=[env["workdir"] + r"\al\cal-split"],
             script=_header(env, "al.export", {"Finsql": env["bc14_finsql"], "ModelTools": env["bc14_model_tools"],
                                               "Server": env["sql_server"], "Database": env["work_database"],
                                               "Filter": "Modified=Yes"}) + FINSQL_FN + r"""
$Dir = Join-Path $Work 'al'; New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Out = Join-Path $Dir 'newsyntax.txt'; $Log = Join-Path $Dir 'finsql_newsyntax.log'
Invoke-Finsql $Finsql "command=exporttonewsyntax, file=`"$Out`", servername=$Server, database=`"$Database`", ntauthentication=yes, filter=$Filter, logfile=`"$Log`"" $Log
Import-Module $ModelTools -Force
$Split = Join-Path $Dir 'cal-split'
if (Test-Path $Split) { Remove-Item $Split -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Split | Out-Null
Split-NAVApplicationObjectFile -Source $Out -Destination $Split -Force
Write-Output ("Objets à convertir : {0}" -f (Get-ChildItem $Split -Filter *.TXT).Count)
"""),
        Step("al.txt2al", "al", "Convertir en AL (Txt2AL)", "powershell",
             "txt2al --rename : un fichier .al par objet dans le projet AL client. Les modifications d'objets "
             "standard deviennent des extensions (tableextension, pageextension) à partir de l'ID indiqué.",
             documentation_url=DOC_TXT2AL, requires=["bc14_txt2al", "al_project"],
             produces=[env["al_project"] + r"\src"],
             script=_header(env, "al.txt2al", {"Txt2al": env["bc14_txt2al"], "AlProject": env["al_project"],
                                               "ExtensionStartId": "50000"}) + r"""
$Source = Join-Path $Work 'al\cal-split'
$Target = Join-Path $AlProject 'src'
New-Item -ItemType Directory -Force -Path $Target | Out-Null
& $Txt2al "--source=$Source" "--target=$Target" --rename "--extensionStartId=$ExtensionStartId" 2>&1 | ForEach-Object { Write-Output $_ }
if ($LASTEXITCODE -ne 0) { throw "txt2al a échoué (code $LASTEXITCODE)" }
Write-Output ("Fichiers AL produits : {0}" -f (Get-ChildItem $Target -Filter *.al -Recurse).Count)
"""),
        Step("al.project", "al", "Préparer le projet AL (VS Code)", "manual",
             "Créer ou compléter app.json (plage d'ID 50000–99999 pour une extension BC Online, dépendances "
             "System/Base Application de la version cible), télécharger les symboles, puis renseigner l'ID, "
             "le nom et l'éditeur de l'extension dans l'écran Environnement.",
             documentation_url=DOC_V14_CUSTOM,
             checklist=["app.json : id, name, publisher, idRanges", "Symboles de la version cible téléchargés",
                        "ID / nom / éditeur saisis dans l'environnement"]),
        Step("al.compile", "al", "Compiler l'extension (alc)", "powershell",
             "Compilation avec alc.exe. Chaque erreur AL est enregistrée dans le journal d'erreurs : une "
             "erreur déjà résolue sur un autre projet affiche sa solution.",
             requires=["alc", "al_project", "al_packages"], produces=[env["ext_app_file"]],
             script=_header(env, "al.compile", {"Alc": env["alc"], "AlProject": env["al_project"],
                                                "Packages": env["al_packages"], "AppFile": env["ext_app_file"]}) + r"""
New-Item -ItemType Directory -Force -Path (Split-Path $AppFile) | Out-Null
& $Alc "/project:$AlProject" "/packagecachepath:$Packages" "/out:$AppFile" 2>&1 | ForEach-Object { Write-Output $_ }
if ($LASTEXITCODE -ne 0) { throw "Compilation en échec (code $LASTEXITCODE)" }
Write-Output "Extension compilée : $AppFile"
"""),
        Step("al.coverage", "al", "Contrôler la couverture des données", "internal",
             "Lit le projet AL et vérifie que chaque table client et chaque champ ajouté à une table "
             "standard existe en AL. Une table absente de l'extension ne sera pas migrée vers BC Online.",
             documentation_url=DOC_MIGRATE, requires=["al_project"]),
    ]


def _bc_upgrade(env: dict, phase: str, from_label: str, old_instance: str, old_admin: str, admin: str,
                instance: str, dvd: str, license_file: str, to_label: str, from14: bool) -> list[Step]:
    p = phase
    base = {"Admin": admin, "Instance": instance, "Server": env["sql_server"], "Database": env["work_database"]}
    dest_apps = ""
    if from14:
        dest_apps = r"""
if ($ExtAppId) {
    $json = '[{"appId":"' + $ExtAppId + '","name":"' + $ExtAppName + '","publisher":"' + $ExtPublisher + '"}]'
    Set-NAVServerConfiguration -ServerInstance $Instance -KeyName DestinationAppsForMigration -KeyValue $json
    Write-Output "DestinationAppsForMigration = $json"
} else { Write-Warning "ID de l'extension client non renseigné : les tables client ne seront pas transférées à l'extension." }
"""
    find_apps = r"""
function Find-App([string]$Root, [string]$Pattern) { Get-ChildItem -Path $Root -Recurse -Filter $Pattern -ErrorAction SilentlyContinue | Select-Object -First 1 }
$Ordered = @('Microsoft_System Application*.app', 'Microsoft_Business Foundation*.app', 'Microsoft_Base Application*.app', 'Microsoft_Application*.app')
$AppFiles = @(); foreach ($pat in $Ordered) { $f = Find-App $Dvd $pat; if ($f) { $AppFiles += $f.FullName } }
if ($ExtAppFile -and (Test-Path $ExtAppFile)) { $AppFiles += $ExtAppFile }
"""
    ext = {"ExtAppId": env["ext_app_id"], "ExtAppName": env["ext_app_name"], "ExtPublisher": env["ext_publisher"],
           "ExtAppFile": env["ext_app_file"], "Dvd": dvd}
    return [
        Step(f"{p}.prepare", p, f"Préparer la base {from_label}", "powershell",
             f"Sur l'instance {from_label} : désinstallation des extensions, dépublication des symboles, "
             "sauvegarde de la base de travail, arrêt de l'instance.",
             documentation_url=DOC_V14_CUSTOM if from14 else DOC_PATHS, destructive=True,
             requires=["sql_server", "work_database", "backup_dir"],
             script=_header(env, f"{p}.prepare", {"OldAdmin": old_admin, "OldInstance": old_instance,
                                                   "Server": env["sql_server"], "Database": env["work_database"],
                                                   "BackupDir": env["backup_dir"]}) + SQL_FN + r"""
Import-Module $OldAdmin -Force
Get-NAVAppInfo -ServerInstance $OldInstance | ForEach-Object {
    Write-Output "Désinstallation $($_.Name) $($_.Version)"
    Uninstall-NAVApp -ServerInstance $OldInstance -Name $_.Name -Version $_.Version -Force
}
Get-NAVAppInfo -ServerInstance $OldInstance -SymbolsOnly | ForEach-Object {
    Write-Output "Dépublication des symboles $($_.Name) $($_.Version)"
    Unpublish-NAVApp -ServerInstance $OldInstance -Name $_.Name -Version $_.Version
}
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$File = Join-Path $BackupDir ("$Database" + '_avant_""" + p + r"""_' + (Get-Date -Format 'yyyyMMdd_HHmm') + '.bak')
Invoke-Sql $Server "BACKUP DATABASE [$Database] TO DISK = N'$File' WITH COPY_ONLY, INIT, CHECKSUM" | Out-Null
Write-Output "Sauvegarde : $File"
Stop-NAVServerInstance -ServerInstance $OldInstance
"""),
        Step(f"{p}.convert", p, f"Conversion technique vers {to_label}", "powershell",
             f"Invoke-NAVApplicationDatabaseConversion avec l'outil d'administration {to_label}.",
             documentation_url=DOC_PATHS, destructive=True, requires=[k for k in ("sql_server", "work_database")],
             script=_header(env, f"{p}.convert", base) + r"""
Import-Module $Admin -Force
Invoke-NAVApplicationDatabaseConversion -DatabaseServer $Server -DatabaseName $Database -Force
Write-Output "Base [$Database] convertie."
"""),
        Step(f"{p}.service", p, f"Raccorder le service {to_label}", "powershell",
             "Base de données de l'instance, licence" + (", DestinationAppsForMigration (transfert des tables "
                                                        "client vers l'extension)" if from14 else "") + ", redémarrage.",
             documentation_url=DOC_DEST_APPS if from14 else DOC_PATHS,
             requires=["work_database"] + (["ext_app_id", "ext_app_name", "ext_publisher"] if from14 else []),
             script=_header(env, f"{p}.service", {**base, **ext, "License": license_file}) + r"""
Import-Module $Admin -Force
Set-NAVServerConfiguration -ServerInstance $Instance -KeyName DatabaseServer -KeyValue $Server
Set-NAVServerConfiguration -ServerInstance $Instance -KeyName DatabaseName -KeyValue $Database
""" + dest_apps + r"""
Restart-NAVServerInstance -ServerInstance $Instance
if ($License) { Import-NAVServerLicense -ServerInstance $Instance -LicenseFile $License; Restart-NAVServerInstance -ServerInstance $Instance }
Write-Output 'Instance configurée.'
"""),
        Step(f"{p}.publish", p, f"Publier les symboles et les applications {to_label}", "powershell",
             "System.app (symboles), System Application, Business Foundation, Base Application, Application "
             "(trouvées dans le DVD) puis l'extension client.",
             documentation_url=DOC_V14_CUSTOM if from14 else DOC_PATHS, requires=[],
             script=_header(env, f"{p}.publish", {**base, **ext}) + r"""
Import-Module $Admin -Force
$Sys = Get-ChildItem -Path $Dvd -Recurse -Filter 'System.app' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Sys) { throw "System.app introuvable dans $Dvd" }
Publish-NAVApp -ServerInstance $Instance -Path $Sys.FullName -PackageType SymbolsOnly
""" + find_apps + r"""
foreach ($f in $AppFiles) {
    Write-Output "Publication : $f"
    Publish-NAVApp -ServerInstance $Instance -Path $f -SkipVerification
}
Get-NAVAppInfo -ServerInstance $Instance | Format-Table Name, Version, Publisher -AutoSize | Out-String | Write-Output
"""),
        Step(f"{p}.appversion", p, "Fixer la version de l'application", "powershell",
             "Set-NAVApplication avec la version de la Base Application publiée.",
             documentation_url=DOC_V14_CUSTOM if from14 else DOC_PATHS, evidence="to_verify",
             script=_header(env, f"{p}.appversion", base) + r"""
Import-Module $Admin -Force
$ba = Get-NAVAppInfo -ServerInstance $Instance -Name 'Base Application' | Sort-Object Version -Descending | Select-Object -First 1
if (-not $ba) { throw 'Base Application non publiée.' }
Set-NAVApplication -ServerInstance $Instance -ApplicationVersion $ba.Version -Force
Write-Output "Version de l'application : $($ba.Version)"
"""),
        Step(f"{p}.sync", p, "Synchroniser le tenant et les applications", "powershell",
             "Sync-NAVTenant puis Sync-NAVApp pour chaque application, dans l'ordre des dépendances.",
             documentation_url=DOC_V14_CUSTOM if from14 else DOC_PATHS, destructive=True,
             script=_header(env, f"{p}.sync", {**base, **ext}) + r"""
Import-Module $Admin -Force
Sync-NAVTenant -ServerInstance $Instance -Mode Sync -Force
""" + find_apps + r"""
foreach ($f in $AppFiles) {
    $i = Get-NAVAppInfo -Path $f
    Write-Output "Synchronisation : $($i.Name) $($i.Version)"
    Sync-NAVApp -ServerInstance $Instance -Name $i.Name -Version $i.Version
}
"""),
        Step(f"{p}.dataupgrade", p, "Mettre à niveau les données des applications", "powershell",
             "Start-NAVAppDataUpgrade quand une version précédente existe, sinon Install-NAVApp.",
             documentation_url=DOC_V14_CUSTOM if from14 else DOC_PATHS, destructive=True,
             script=_header(env, f"{p}.dataupgrade", {**base, **ext}) + find_apps + r"""
Import-Module $Admin -Force
foreach ($f in $AppFiles) {
    $i = Get-NAVAppInfo -Path $f
    $t = Get-NAVAppInfo -ServerInstance $Instance -Name $i.Name -Version $i.Version -Tenant default -TenantSpecificProperties
    if ($t.ExtensionDataVersion -and ($t.ExtensionDataVersion -ne $t.Version)) {
        Write-Output "Mise à niveau des données : $($i.Name) $($t.ExtensionDataVersion) -> $($i.Version)"
        Start-NAVAppDataUpgrade -ServerInstance $Instance -Name $i.Name -Version $i.Version
    } elseif (-not $t.IsInstalled) {
        Write-Output "Installation : $($i.Name) $($i.Version)"
        Install-NAVApp -ServerInstance $Instance -Name $i.Name -Version $i.Version
    } else { Write-Output "Déjà à jour : $($i.Name)" }
}
Get-NAVAppInfo -ServerInstance $Instance -Tenant default -TenantSpecificProperties | Format-Table Name, Version, IsInstalled, ExtensionDataVersion -AutoSize | Out-String | Write-Output
"""),
        Step(f"{p}.check", p, f"Contrôler {to_label}", "manual",
             f"Ouvrir le client web {to_label} et vérifier les points clés.",
             checklist=[f"Connexion au client {to_label}", "Extension client installée",
                        "Tables client présentes avec leurs données", "Comptages relancés (phase Validation)"]),
    ]


def _cloud(env: dict) -> list[Step]:
    return [
        Step("cloud.readiness", "cloud", "Vérifier l'éligibilité BC Online", "internal",
             "Synthèse : fonctions indisponibles en SaaS restantes, tables et champs client sans équivalent AL, "
             "étapes précédentes non terminées.", documentation_url=DOC_MIGRATE),
        Step("cloud.extension", "cloud", "Déployer l'extension client dans BC Online", "manual",
             "Publier l'extension (PTE) d'abord dans un environnement sandbox, puis en production, depuis "
             "VS Code ou le centre d'administration.",
             checklist=["Extension installée en sandbox", "Tests fonctionnels en sandbox",
                        "Extension installée dans l'environnement de production"]),
        Step("cloud.setup", "cloud", "Configurer la migration cloud", "manual",
             "Dans BC Online : assistant « Configuration de la migration cloud », produit source Business Central "
             "(version courante), chaîne de connexion SQL de la base de travail, runtime d'intégration "
             "auto-hébergé installé sur la machine SQL avec la clé fournie par l'assistant.",
             documentation_url=DOC_CLOUD,
             checklist=["Runtime d'intégration installé et connecté", "Sociétés à migrer sélectionnées",
                        "Utilisateurs BC Online créés"]),
        Step("cloud.replicate", "cloud", "Répliquer puis mettre à niveau les données", "manual",
             "Lancer la réplication depuis « Gestion de la migration cloud », suivre son statut, puis lancer la "
             "mise à niveau des données dans BC Online. Seules les tables présentes à la fois dans la source et "
             "dans BC Online sont migrées.", documentation_url=DOC_MIGRATE,
             checklist=["Réplication terminée sans erreur", "Mise à niveau des données terminée",
                        "Erreurs de réplication reportées dans le journal d'erreurs"]),
        Step("cloud.finish", "cloud", "Clôturer la migration cloud", "manual",
             "Désactiver la migration cloud, contrôler les autorisations et les paramètres, ouvrir l'accès.",
             documentation_url=DOC_CLOUD,
             checklist=["Migration cloud désactivée", "Autorisations utilisateurs vérifiées", "Recette signée"]),
    ]


def _validation(env: dict) -> list[Step]:
    def count(step_id: str, db_key: str, name: str, title: str) -> Step:
        return Step(step_id, "validation", title, "powershell",
                    "Nombre d'enregistrements par table SQL, exporté en CSV pour la comparaison.",
                    requires=["sql_server", db_key], produces=[env["workdir"] + rf"\validation\rowcount_{name}.csv"],
                    script=_header(env, step_id, {"Server": env["sql_server"], "Database": env[db_key]}) + SQL_FN + f"""
$Dir = Join-Path $Work 'validation'; New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$t = Invoke-Sql $Server "{ROWCOUNT_SQL}" $Database
$t | Export-Csv -Path (Join-Path $Dir 'rowcount_{name}.csv') -NoTypeInformation -Encoding UTF8
Write-Output ("Tables comptées : {{0}}" -f $t.Rows.Count)
""")
    return [
        count("validation.count_source", "nav_database", "source", "Compter les enregistrements de la base NAV"),
        count("validation.count_target", "work_database", "target", "Compter les enregistrements après mise à niveau"),
        Step("validation.compare", "validation", "Comparer les volumes source / cible", "internal",
             "Rapproche les tables par société et par nom (le suffixe d'application des tables BC est ignoré) et "
             "signale les écarts."),
        Step("validation.reports", "validation", "Recette des rapports", "manual",
             "Pour chaque rapport migré : comparaison du dataset puis du PDF NAV / BC sur les mêmes données.",
             checklist=["Rapports critiques comparés (facture, BL, commande)", "Codes-barres vérifiés",
                        "Écarts consignés dans le journal d'erreurs"]),
    ]


def build_plan(project: dict, env: dict) -> dict:
    path = compute_path(project["source_version"], project["target_version"], bool(project["target_online"]))
    steps: list[Step] = _prep(env)
    targets = [h["to_version"] for h in path["hops"]]
    if "NAV 2013" in targets:
        steps += _nav2013()
    has_bc14_hop = "BC 14" in targets
    if has_bc14_hop:
        steps += _bc14(env)
    if project["target_version"] >= 15 and (has_bc14_hop or path["source"] == "BC 14"):
        steps += _al(env)
    from14 = has_bc14_hop or path["source"] == "BC 14"
    previous = ("BC 14", env["bc14_instance"], env["bc14_admin"])
    if "BC 25" in targets:
        steps += _bc_upgrade(env, "bc25", "BC 14", env["bc14_instance"], env["bc14_admin"], env["bc25_admin"],
                             env["bc25_instance"], env["bc25_dvd"], env["bc25_license"], "BC 25", from14)
        previous, from14 = ("BC 25", env["bc25_instance"], env["bc25_admin"]), False
    target_label = f"BC {project['target_version']}"
    if target_label in targets and target_label != "BC 25":
        steps += _bc_upgrade(env, "target", previous[0], previous[1], previous[2], env["target_admin"],
                             env["target_instance"], env["target_dvd"], env["target_license"], target_label, from14)
    if project["target_online"]:
        steps += _cloud(env)
    steps += _validation(env)
    for i, s in enumerate(steps, 1):
        s.order = i  # type: ignore[attr-defined]
    phases = []
    for s in steps:
        if not phases or phases[-1]["id"] != s.phase:
            phases.append({"id": s.phase, "label": PHASES[s.phase], "steps": []})
        d = s.to_dict()
        d["order"] = s.order  # type: ignore[attr-defined]
        phases[-1]["steps"].append(d)
    return {"upgrade_path": path, "phases": phases, "step_count": len(steps)}


def find_step(plan: dict, step_id: str) -> dict | None:
    for ph in plan["phases"]:
        for s in ph["steps"]:
            if s["id"] == step_id:
                return s
    return None
