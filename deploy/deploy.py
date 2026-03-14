import argparse
import os
import dataclasses
import abc
import typing
import logging
import time
import socket
import json

# TODO: Actually configure logging
logging.basicConfig(level=logging.DEBUG, filename="toolsWebsiteDeployment.log", filemode="w")
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)

def raiseCriticalException(msg:str, result: SSHClient.CommandResult | None = None):
    logging.error(msg)
    if result:
        logging.error("SSH Result Std Out: %s", result.stdout)
        logging.error("SSH Result Std Err: %s", result.stderr)
    e = Exception()
    e.add_note(msg)
    raise e
try:
    import paramiko
except:
    raiseCriticalException("Could not import Paramiko. This is likely because it is not installed on this machine. Paramiko is used to ssh into the remote machine. Install paramiko with 'pip install paramiko'")

class Constants:
    AUSTIN_DSA_TOOLS_GITHUB_CLONE = "https://github.com/Austin-DSA/GeneralToolsWebsite.git"

    TOOLS_DIR = "Tools"
    REPO_DIR = "Repo"
    RUNNING_DIR = "Running"

    # Can't use os.path.join becuase this may be running on a Windows Machine locally and os.path.join would create the paths incorrectly
    WEBSITE_USER = "tools-website"
    WEBSITE_USER_HOME = f"/home/{WEBSITE_USER}"
    CLONE_DIR = f"{WEBSITE_USER_HOME}/{TOOLS_DIR}/{REPO_DIR}"
    GIT_DIR = f"{WEBSITE_USER_HOME}/{TOOLS_DIR}/{REPO_DIR}/GeneralToolsWebsite"
    WORKING_DIR = f"{WEBSITE_USER_HOME}/{TOOLS_DIR}/{RUNNING_DIR}"
    NGINX_ERROR_LOG = f"{TOOLS_DIR}/nginxError.log"
    

    SECRETS_JSON_PATH = f"{WORKING_DIR}/GeneralToolsWebsite/tools/SecretManager/secrets.json"
    SECRETS_SERVICE_KEY_PATH = f"{WORKING_DIR}/GeneralToolsWebsite/tools/SecretManager/serviceKey.json"
    DEV_ENV_PATH = f"{WORKING_DIR}/GeneralToolsWebsite/dev-env.env"
    PROD_ENV_PATH = f"{WORKING_DIR}/GeneralToolsWebsite/.env"
    DOCKER_DIR = f"{WORKING_DIR}/GeneralToolsWebsite"

@dataclasses.dataclass
class Flags:
    mode: str
    sshIp : str
    rootUser : str
    rootPassword : str
    websitePassword : str
    websiteToolsVersion: str
    websiteDomain: str
    secretsJsonPath: str
    googleServiceKeyPath: str
    adminUsername : str
    adminPassword : str
    sshPort : int = 22
    regenerateDHCert: bool = False

    @staticmethod
    def parseFlagsFromFile(path: str) -> Flags:
        with open(path,mode="r") as f:
            d = json.load(f)
            return Flags(**d)

    @staticmethod
    def generateTemplateFiles(argFileTemplatePath: str, secretsFileTemplate: str):
        flags = Flags(
            mode="Deploy",
            sshIp="",
            rootUser="",
            rootPassword="",
            websitePassword="",
            websiteToolsVersion="",
            websiteDomain="",
            secretsJsonPath=secretsFileTemplate,
            googleServiceKeyPath="",
            adminUsername="",
            adminPassword=""
        )
        with open(argFileTemplatePath, mode="w") as f:
            json.dump(dataclasses.asdict(flags),f, indent=1)
        secrets = {}
        secrets["ZoomAccountId"] = ""
        secrets["ZoomClientId"] = ""
        secrets["ZoomClientSecret"] = ""
        secrets["AnUsername"] = ""
        secrets["AnPassword"] = ""
        secrets["GoogleCalId"] = ""
        secrets["GoogleDelegateAccount"] = ""
        secrets["WebsiteEmailAccountUsername"] = ""
        secrets["WebsiteEmailAccountPassword"] = ""
        with open(secretsFileTemplate,mode="w") as f:
            json.dump(secrets,f,indent=1)

    @staticmethod 
    def parseFlags() -> Flags:
        parser = argparse.ArgumentParser(
            prog="Austin DSA Tools Website Deployer",
            description="Script to help automate installing and starting the Austin DSA Tools Website",
            epilog="If any help is needed please contact the current Austin DSA IT Coordinator on Slack or via tech@austindsa.org"
        )
        parser.add_argument("--mode", type=str, help="CreateArgFile - Creates a template file to fill arguments and a file for secrets\n Deploy - Run a full deployment")
        parser.add_argument("--arg-file", type=str, help="Read all arguments from a file instead of the command line.")
        parser.add_argument("--arg-file-template", type=str, help="If mode == CreateArgFile then this will be the output file for the template args")
        parser.add_argument("--secrets-file-template", type=str, help="If mode == CreateArgFile then this will be the template output file for the secrets")
        parser.add_argument("--only-run", action="store_true", help="Have the script stop the website if running and then start it again. This assumes it has already been deployed")
        parser.add_argument("--version", type=str, help="The version tag of the website to deploy. If not supplied than whatever verison is currently on the machine will be used.")
        parser.add_argument("--ssh-ip", type=str, help="The IP address to ssh into if wanting remote deployment. If present the machine running this script will either need an ssh key into the remote machine or a password will need to be supplied with --password")
        parser.add_argument("--ssh-port", type=int, help="Port to connect to for SSH", default=22)
        parser.add_argument("--root-user", type=str, help="The root user for the remote machine")
        parser.add_argument("--root-password", type=str, help="The password for the root user. Used for ssh and sudo.")
        parser.add_argument("--website-password", type=str, help="The password for the website user. If the website user already exists need this to sign in. If it doesnt this will be used when setting it up")
        parser.add_argument("--website-version", type=str, help="The branch or tag to checkout for the tools website repo")
        parser.add_argument("--website-domain",type=str, help="The domain the website will be living at e.g tools.austindsa.org")
        parser.add_argument("--force-regen-dh",action="store_true", help="Regenerate the DH certificate even if it already exists. This may take some time.")
        parser.add_argument("--secrets-json-path",type=str, help="Path on the local machine for the json secrets file")
        parser.add_argument("--google-service-key-path", type=str, help="Path on the local machine for the google service key")
        args = parser.parse_args()

        if args.mode == "CreateArgFile":
            if args.arg_file_template is None or args.secrets_file_template is None:
                raiseCriticalException("When using the CreateArgFile mode must provide --arg-file-template and --secrets-file-template")
            Flags.generateTemplateFiles(argFileTemplatePath=args.arg_file_template, secretsFileTemplate=args.secrets_file_template)
            exit(0)

        if args.arg_file is not None:
            return Flags.parseFlagsFromFile(args.arg_file)
        return Flags(
            mode=args.mode,
            sshIp=args.ssh_ip, 
            rootUser=args.root_user, 
            rootPassword=args.root_password, 
            websitePassword=args.website_password, 
            sshPort=args.ssh_port,
            websiteToolsVersion=args.website_version,
            websiteDomain=args.website_domain,
            regenerateDHCert=args.force_regen_dh,
            secretsJsonPath=args.secrets_json_path,
            googleServiceKeyPath=args.google_service_key_path
            )


# TODO: Refactor to rely less on global singletons and instead pass things around
@dataclasses.dataclass
class SSHCommandResult:
    stdout: str
    stderr: str
    # stdIn: typing.IO
    exitStatus: int
    def success(self) -> bool:
        return self.exitStatus == 0 

@dataclasses.dataclass
class SSHClientEntry:
    user: str
    password: str
    isRoot: bool
    ip: str
    port: int
    client: SSHClient | None

class SSHClient:

    def __init__(self, entry: SSHClientEntry):
        self.entry = entry
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.load_system_host_keys()
        logging.info("Creating SSH connection to %s", self.entry.ip)
        self.client.connect(
            hostname=self.entry.ip,
            port=self.entry.port,
            username=self.entry.user,
            password=self.entry.password
            )

    def reconnect(self):
        for _ in range(3):
            logging.info("Sleeping for 10 seconds then attempting to reconnect")
            time.sleep(10)
            try:
                self.client.connect(
                    hostname=self.entry.ip,
                    port=self.entry.port,
                    username=self.entry.user,
                    password=self.entry.password
                )
                logging.info("Reconnected successfully")
                return
            except (socket.error, paramiko.SSHException, EOFError) as e:
                logging.error("Failed to connect")
        raiseCriticalException("Failed to reconnect after reboot")

    def execCommand(self, command:str, supressLogs: bool = False) -> SSHCommandResult:
        logging.info("Executing: %s", command)
        # TODO: Clean up the output and log to verbose so the file logs will see the output but not the console
        # stdin,stdout,stderr = self.client.exec_command(command=command)

        # stdoutText = stdout.read().decode('utf-8')
        # stderrText = stderr.read().decode('utf-8')
        # exitStatus = stdout.channel.recv_exit_status()
        # stdout.close()
        # stderr.close()
        # logging.info(stdoutText)
        # logging.info(stderrText)
        # Using the channel directly so we can get output faster which can help with seeing deadlocks
        stdout = ""
        stderr = ""
        with self.client.get_transport().open_channel("session") as c:
            # c.send(f"{command}\n")
            c.exec_command(command)
            while not c.exit_status_ready():
                if c.recv_ready():
                    output = c.recv(1000).decode('utf-8')
                    if not supressLogs:
                        logging.debug(output)
                    stdout += output
                if c.recv_stderr_ready():
                    err = c.recv_stderr(1000).decode('utf-8')
                    if not supressLogs:
                        logging.debug(err)
                    stderr += err
            # Grab any left over stdout/stdin
            while c.recv_ready():
                output = c.recv(1000).decode('utf-8')
                if not supressLogs:
                    logging.debug(output)
                stdout += output
            while c.recv_stderr_ready():
                err = c.recv_stderr(1000).decode('utf-8')
                if not supressLogs:
                    logging.debug(err)
                stderr += err
            exitStatus = c.recv_exit_status()
        # Originally attempted to enter the sudo password
        # But root doesn't need sudo, so will first attempt just not using it but leaving code around if we need it
        # Can't just blindly send cause paramiko can crash if we write to stdin at the wrong time
        # needsSudo = command.strip().startswith("sudo")
        # if needsSudo is True:
        #     stdin.write(self.password+'\n')
        #     stdin.flush()

        return SSHCommandResult(stdout,stderr,exitStatus)

    def close(self):
        self.client.close()

    def uploadFile(self, localFilePath: str, remoteFilePath: str):
        logging.info("Uploading %s to %s for %s", localFilePath, remoteFilePath, self.entry)
        if not os.path.exists(localFilePath):
            raiseCriticalException(f"Attempting to upload {localFilePath} but does not exist")

        sftpClient = self.client.open_sftp()
        if sftpClient is None:
            raiseCriticalException(f"Failed to open sftp client for {self.entry}")
        sftpClient.put(localpath=localFilePath, remotepath=remoteFilePath, confirm=False)
        sftpClient.close()

    def createDirectoryIfDNE(self, directory: str):
        result = self.execCommand(f"test -d {directory}")
        if not result.success():
            logging.info("Directory %s does not exist, creating", directory)
            result = self.execCommand(f"mkdir -p {directory}")
            if not result.success():
                raiseCriticalException(f"Could not create directory {directory}")    


    _rootClient: SSHClient | None = None
    _websiteClient: SSHClient | None = None
    
    # TODO: This assumes all the users are on the same remote machine
    # This is fine for now but could cause a subtle bug where if there are multiple machines a reboot command will choose the wrong one 
    _clients: dict[str, SSHClientEntry] = {}

    # Adds a user and password to client registry
    # This is so tasks can look up the ssh client by name
    # This won't open the connection, those will be opened lazily as clients are requested by tasks
    # This needs to be done lazily as some tasks may add users so initiating them before that runs 
    # Attempting to look up a client that doesn't exist will crash so be sure to register a client before
    # Marking a client as root will handle reboots
    @staticmethod
    def registerClient(clientEntry: SSHClientEntry):
        SSHClient._clients[clientEntry.user] = clientEntry

    @staticmethod
    def reboot():
        logging.info("Rebooting remote machine")
        logging.info("Finding root client")
        rootEntry = None
        for user, entry in SSHClient._clients.items():
            if entry.isRoot:
                rootEntry = entry
                break
        if rootEntry is None or rootEntry.client is None:
            raiseCriticalException("Couldn't find root client to reboot")

        # Close all connections except the root
        for user,entry in SSHClient._clients.items():
            if entry.client is not None and user != rootEntry.user:
                entry.client.close()
        
        # Reboot
        rootEntry.client.execCommand("/sbin/reboot -f > /dev/null 2>&1 &")
        rootEntry.client.close()

        # Reconnect all clients
        for user,entry in SSHClient._clients.items():
            if entry.client is not None:
                entry.client.reconnect()

    @staticmethod
    def client(user: str) -> SSHClient:
        if user not in SSHClient._clients:
            raiseCriticalException(f"Attempted to access ssh client {user} which is not registered")
        entry = SSHClient._clients[user]
        if entry.client is None:
            entry.client = SSHClient(entry=entry)
        return entry.client

    @staticmethod
    def closeClients():
        for user, entry in SSHClient._clients.items():
            if entry.client:
                entry.client.close()

# TODO: It seems a bit overkill right now to pass everything in including which user to run commands on
# However I could see us easily re-using this or maybe packaging it in a separate repo so that other chapters can use it
# Its not perfect right now but this setup should make converting this to a more generic deploy tool in the future a bit easier
class SetupTask:

    @abc.abstractmethod
    def name(self):
        pass

    # Client can be used to determine which user to run the command on
    @abc.abstractmethod
    def runTask(self):
        pass

#SECTION - General Droplet Management Tasks

# Runs apt update && apt upgrade and reboots the remote machine if required
class AptUpdateUpgrade(SetupTask):

    def __init__(self, root: str):
        self.root = root

    def name(self) -> str:
        return "Apt Update Upgrade"

    def runTask(self):
        logging.info("Running apt update and upgrade")
        client = SSHClient.client(user=self.root)
        result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get update")
        if not result.success():
            raiseCriticalException("Failed to run apt-get update")

        result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get -y upgrade")
        if not result.success():
            raiseCriticalException("Failed to run apt-get upgrade")
        
        result = client.execCommand("test -e /var/run/reboot-required")
        if result.success():
            logging.info("Reboot required")
            SSHClient.reboot()
    

# Will create a user to actually run the website
# In the outline guide made that user a sudoer but don't need to here since the computer can handle switching between the users easily
class CreateWebsiteUser(SetupTask):
    def __init__(self, root: str, userToCreate: str, userToCreatePassword: str):
        self.root = root
        self.userToCreate = userToCreate
        self.userToCreatePassword = userToCreatePassword

    def name(self) -> str:
        return "Creating website user"
    
    def runTask(self):
        logging.info("Check if website user already exists")
        client = SSHClient.client(user=self.root)
        result = client.execCommand("compgen -u")
        if not result.success():
            raiseCriticalException("Couldn't query the existing user list")
        if self.userToCreate in result.stdout:
            logging.info("%s already exists no need to create", self.userToCreate)
            return

        logging.info("Creating %s user", self.userToCreate)

        result = client.execCommand(f"useradd -m {self.userToCreate}")
        if not result.success():
            raiseCriticalException(f"Failed to create user {self.userToCreate}")
        result = client.execCommand(f"echo {self.userToCreate}:{self.userToCreatePassword} | chpasswd")
        if not result.success():
            raiseCriticalException(f"Failed to set password for {self.userToCreate}")
        logging.info(f"Successfully created user {self.userToCreate}")


# TODO: Maybe allow context passing, but try without since this should hopefully be simple
# Will create two directoires inside home
# CleanRepo and Running
# CleanRepo is a git repo that is just used as a reference and to get new updates
# Running will contain the actual running instance
# Making two copies allows us to make modifications to the running instance without affecting the git repo
# This way if we wanted to update without changing the secrets you can just git pull and then copy over
# Will also clone the git repo into the repo directory
class CreateDirectories(SetupTask):
    def __init__(self, user: str):
        self.user = user

    def name(self) -> str:
        return "Initial Directory Creation"

    def runTask(self):
        # Setup the directories 
        # Navigate to home directory
        client = SSHClient.client(user=self.user)
        result = client.execCommand("cd")
        if not result.success():
            raiseCriticalException("Failed to switch to home directory")

        client.createDirectoryIfDNE(Constants.CLONE_DIR)
        client.createDirectoryIfDNE(Constants.WORKING_DIR)

# All tasks will assume the current directory is the home directory

# Git it already installed on Digital Ocean Droplet
class InstallCoreUtils(SetupTask):

    def __init__(self, root: str):
        self.root = root

    def name(self)->str:
        return "Install Core Utils"

    def runTask(self):
        client = SSHClient.client(user=self.root)
        result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get -y install apt-transport-https ca-certificates curl software-properties-common nano wget zip unzip gnupg python3.12-venv")
        if not result.success:
            raiseCriticalException("Failed to install core utils")
        
        result = client.execCommand("test -e /var/run/reboot-required")
        if result.success():
            logging.info("Reboot required")
            SSHClient.reboot()

#!SECTION

#SECTION - UFW
#TODO: Add UFW, Skipping UFW for now since it sometimes broke the wiki at one point
class UFW(SetupTask):
    def __init__(self):
        pass

    def name(self)->str:
        pass

    def runTask(self):
        pass

#!SECTION

#SECTION - Git Tasks

# Clones the repo into the Repo directory
class CloneRepo(SetupTask):
    def __init__(self, user: str, cloneDir: str, gitDir: str, remote: str):
        self.user = user
        self.cloneDir = cloneDir
        self.remote = remote
        self.gitDir = gitDir

    def name(self)->str:
        return "Clone Tools Repo"

    def runTask(self):
        # Check if the repo already exists
        logging.info("Check if tools repo already exists")
        client = SSHClient.client(user=self.user)
        result = client.execCommand(f"git -C {self.gitDir} config --list")
        if result.success() and len(result.stdout.strip()) > 0:
            remoteOriginURL = ""
            for l in result.stdout.splitlines():
                if l.startswith("remote.origin.url"):
                    remoteOriginURL = l.split("=")[1].strip()
                    break
            if remoteOriginURL != self.remote:
                raiseCriticalException(f"A git repo already exists in the repo dir but with incorrect origin {remoteOriginURL}")
            return
        
        # Clone the repo
        logging.info("Cloning Tools repo")
        result = client.execCommand(f"git -C {self.cloneDir} clone --recursive {self.remote}")
        if not result.success():
            raiseCriticalException("Failed to clone tools repo")

# TODO: Might not be needed?
class PullRepo(SetupTask):
    def __init__(self, user: str, gitDir: str):
        self.user = user
        self.gitDir = gitDir

    def name(self)->str:
        return "Pull Repo"
    
    def runTask(self):
        logging.info("Pulling tools repo")
        client = SSHClient.client(user=self.user)
        result = client.execCommand(f"git -C {self.gitDir} pull -f")
        if not result.success():
            raiseCriticalException("Failed to pull tools repo")

class FetchRepo(SetupTask):
    def __init__(self, user: str, gitDir: str):
        self.user = user
        self.gitDir = gitDir

    def name(self)->str:
        return "Fetch Repo"
    
    def runTask(self):
        logging.info("Fetching tools repo")
        client = SSHClient.client(self.user)
        result = client.execCommand(f"git -C {self.gitDir} fetch origin")
        if not result.success():
            raiseCriticalException("Failed to fetch tools repo")

class CheckoutToolsRepoVersion(SetupTask):
    def __init__(self, user:str, gitDir: str, tagOrBranch: str):
        self.user = user
        self.gitDir = gitDir
        self.tagOrBranch = tagOrBranch

    def name(self)->str:
        return f"Check out {self.tagOrBranch} for tools repo"

    def runTask(self):
        logging.info("Checking out %s for %s repo", self.tagOrBranch, self.gitDir)
        client = SSHClient.client(user=self.user)
        result = client.execCommand(f"git -C {self.gitDir} checkout -f {self.tagOrBranch}")
        if not result.success():
            raiseCriticalException(f"Failed to checkout {self.tagOrBranch} for tools repo")

class OverwriteRepoToWorkingDirectory(SetupTask):
    def __init__(self, user: str, gitDir: str, workingDir: str):
        self.user = user
        self.gitDir = gitDir
        self.workingDir = workingDir

    def name(self) ->str:
        return "Copy over repo to working directory"
    
    def runTask(self):
        logging.info("Deleting working directory to ensure no left over files exist")
        client = SSHClient.client(user=self.user)
        result = client.execCommand(f"rm -f -r {self.workingDir}/*")
        logging.info("Copying over %s to %s", self.gitDir, self.workingDir)
        result = client.execCommand(f"cp -r -f {self.gitDir} {self.workingDir}")
        if not result.success():
            raiseCriticalException("Failed to copy repo dir to working dir")

#!SECTION

#SECTION - Snap Tasks

class InstallSnap(SetupTask):
    def __init__(self, root: str):
        self.root = root

    def name(self)->str:
        return "Installing Snap"

    def runTask(self):
        logging.info("Installing Snap")
        client = SSHClient.client(user=self.root)
        result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get -y install snapd")
        if not result.success():
            raiseCriticalException("Failed to install snap")
        
        logging.info("Installing Snap Core")
        result = client.execCommand("snap install core")
        if not result.success():
            raiseCriticalException("Failed to install snap core")
        
        logging.info("Refreshing Snap Core")
        result = client.execCommand("snap refresh core")
        if not result.success():
            raiseCriticalException("Failed to refresh snap core")
        
        result = client.execCommand("test -e /var/run/reboot-required")
        if result.success():
            logging.info("Reboot required")
            SSHClient.reboot()

#!SECTION

#SECTION - Nginx

class InstallNginx(SetupTask):

    def __init__(self, root: str):
        self.root = root

    def name(self)->str:
        return "Install Nginx"

    def runTask(self):
        client = SSHClient.client(user=self.root)
        result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get -y install nginx")
        if not result.success:
            raiseCriticalException("Failed to install nginx")
        
        result = client.execCommand("test -e /var/run/reboot-required")
        if result.success():
            logging.info("Reboot required")
            SSHClient.reboot()

class ConfigureNginx(SetupTask):

    
    def __init__(self, root: str, websiteUser: str, domain: str, errorLogPath: str,  regenerateDHCert: bool):
        self.root = root
        self.websiteUser = websiteUser
        self.domain = domain
        self.errorLogPath = errorLogPath
        self.regenerateDHCert = regenerateDHCert

    def name(self) -> str:
        return "Configure Nginx"
    
    def runTask(self):
        # TODO: Eventually should probably move this into a file inside the django app
        configFile = f"""
server {{
    listen 80;
    listen [::]:80;
 
    server_name {self.domain};
 
    return 301 https://$host$request_uri;
}}
 
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
 
    server_name {self.domain};
 
    access_log off;
    error_log /home/{self.websiteUser}/{self.errorLogPath};
 
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA256:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES256-SHA384:ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA384:ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:DHE-RSA-AES128-SHA256:DHE-RSA-AES128-SHA:DHE-RSA-AES256-SHA256:DHE-RSA-AES256-SHA:ECDHE-ECDSA-DES-CBC3-SHA:ECDHE-RSA-DES-CBC3-SHA:EDH-RSA-DES-CBC3-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA256:AES256-SHA256:AES128-SHA:AES256-SHA:DES-CBC3-SHA:!DSS;
    ssl_prefer_server_ciphers on;
    ssl_session_timeout 24h;
    ssl_stapling on;
    ssl_stapling_verify on;
    ssl_certificate /etc/letsencrypt/live/{self.domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{self.domain}/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/{self.domain}/chain.pem;
    ssl_dhparam /etc/ssl/certs/dhparam.pem;
    ssl_session_cache shared:OutlineSSL:10m;
    ssl_session_tickets off;
 
    location /static/ {{
        alias /var/www/tools-website/static;
    }}

    location / {{
        proxy_pass http://127.0.0.1:3000;
         
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Forwarded-Proto $scheme;
 
        proxy_redirect off;
    }}
}}
    """

        logging.info("Creating DH Exchange key")
        # This command prints out a lot of chars and the SSL seems to grab it one by one causing tons and tons of log lines
        # Its all just "." and "+" so nothing worthwile so just suppress the logs
        client = SSHClient.client(user=self.root)
        result = client.execCommand("test -e /etc/ssl/certs/dhparam.pem")
        if not result.success() or self.regenerateDHCert:
            logging.info("Generating DH cert")
            result = client.execCommand("openssl dhparam -out /etc/ssl/certs/dhparam.pem 2048", supressLogs=True)
            if not result.success():
                raiseCriticalException("Failed to create DH Exchange Key")

        logging.info("Writing Config File")
        # Need to use Single qoutes for the config so bash doesn't delete the $ character
        result = client.execCommand(f"echo '{configFile}' > /etc/nginx/conf.d/tools-website.conf")
        if not result.success():
            raiseCriticalException("Failed to write Nginx config file")

        logging.info("Verify Syntax")
        result = client.execCommand("nginx -t")
        if not result.success():
            raiseCriticalException("Nginx syntax invalid")

class StopNginx(SetupTask):
    def __init__(self, root: str):
        self.root = root
    def name(self):
        return "Stopping Nginx"
    def runTask(self):
        SSHClient.client(self.root).execCommand("systemctl stop nginx")

class StartNginx(SetupTask):
    def __init__(self, root: str):
        self.root = root
    def name(self):
        return "Stopping Nginx"
    def runTask(self):
        SSHClient.client(self.root).execCommand("systemctl start nginx")

class RestartNginx(SetupTask):
    def __init__(self, root: str):
        self.root = root
    def name(self):
        return "Restarting Nginx"
    def runTask(self):
        SSHClient.client(self.root).execCommand("systemctl restart nginx")

#!SECTION

#SECTION - Lets Encrypt/SSL
class InstallCertbot(SetupTask):
    def __init__(self, root: str):
        self.root = root

    def name(self)->str:
        return "Installing Certbot"

    def runTask(self):
        logging.info("Installing Certbot")
        client = SSHClient.client(user=self.root)
        result = client.execCommand("snap install --classic certbot")
        if not result.success():
            raiseCriticalException("Failed to install certbot")
        
        logging.info("Check if cert bot link already exists")
        result = client.execCommand("test -e /usr/bin/certbot")
        if result.success():
            logging.info("Cert bot link already exists")
            return
        logging.info("Creating cert bot link")
        result = client.execCommand("ln -s /snap/bin/certbot /usr/bin/certbot")
        if not result.success():
            raiseCriticalException("Failed to link certbot")

class LetsEncrypt(SetupTask):

    def __init__(self, root: str, domain: str):
        self.root = root
        self.domain = domain

    def name(self)->str:
        return "Setting up SSL with Lets Encrypt"

    def runTask(self):
        logging.info("Checking if LetsEncrypt SSL Cert already exists for %s", self.domain)
        client = SSHClient.client(user=self.root)
        result = client.execCommand(f"test -e /etc/letsencrypt/live/{self.domain}/fullchain.pem")
        if result.success():
            logging.info("SSL/LetsEncrypt already set up for %s", self.domain)
            return
        
        logging.info("Installing SSL certificate with certbot for domain %s", self.domain)
        result = client.execCommand(f'certbot certonly --standalone -n --agree-tos --no-eff-email --staple-ocsp --preferred-challenges http -m noreply@{self.domain} -d {self.domain} --pre-hook="systemctl stop nginx" --post-hook="systemctl start nginx"')
        if not result.success():
            raiseCriticalException("Failed to install SSL certificate with cert bot for domain %s", self.domain)
        
        result = client.execCommand(f"test -e /etc/letsencrypt/live/{self.domain}/fullchain.pem")
        if not result.success():
            logging.info("Couldn't find SSL cert for  %s after installation", self.domain)
            return

#!SECTION



#SECTION - Docker

class InstallDocker(SetupTask):

    def __init__(self, root: str):
        self.root = root

    def name(self)->str:
        return "Install Docker"

    def runTask(self):
        # Check if docker is already installed
        client = SSHClient.client(user=self.root)
        dockerInstalled = client.execCommand("docker --version")
        dockerComposeInstalled = client.execCommand("docker compose version")
        if dockerInstalled.success() and dockerComposeInstalled.success():
            logging.info("Docker and Docker compose already installed")
            return
    
        if not dockerInstalled.success():
            logging.info("Installing Docker")
            logging.info("Dockers GPG key wasn't found, adding to keyring")
            result = client.execCommand("curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --batch --yes --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg")
            if not result.success():
                raiseCriticalException("Failed to install docker's gpg key")
            
            logging.info("Docker's repo list not found in apt, adding")
            result = client.execCommand('echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null')
            if not result.success():
                raiseCriticalException("Failed to add docker to the apt list")
            
            result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get -y update")

            result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get -y install docker-ce")
            if not result.success():
                raiseCriticalException("Failed to install docker")
        
        result = client.execCommand("DEBIAN_FRONTEND=noninteractive apt-get -y install docker-ce-cli containerd.io docker-compose-plugin docker-compose")
        if not result.success():
            raiseCriticalException("Failed to install docker compose")

        result = client.execCommand("docker compose version")
        if not result.success():
            raiseCriticalException("Failed to install docker compose")




class AddUserToDockerGroup(SetupTask):

    def __init__(self, root:str, userToAdd: str):
        self.root = root
        self.userToAdd = userToAdd

    def name(self)->str:
        return "Add User to Docker Group"

    def runTask(self):
        # Check if docker is already installed
        websiteClient = SSHClient.client(user=self.userToAdd)
        result = websiteClient.execCommand("groups")
        if not result.success():
            raiseCriticalException("Failed to list user's current groups")
        if "docker" in result.stdout.split():
            logging.info("%s is already in the docker group", self.userToAdd)
            return
        
        logging.info("Adding %s to docker group", self.userToAdd)
        rootClient = SSHClient.client(user=self.root())
        result = rootClient.execCommand(f"usermod -aG docker {self.userToAdd}")
        if not result.success():
            raiseCriticalException("Failed to add %s to docker group", self.userToAdd)

#!SECTION


#SECTION - Website

class CopyOverWebsiteSecrets(SetupTask):

    def __init__(self, user: str, secretsJsonPath: str, googleServiceKeyPath: str):
        self.user = user
        self.secretsJsonPath = secretsJsonPath
        self.googleServiceKeyPath = googleServiceKeyPath


    def name(self)->str:
        return "Copy Over Secrets"

    def runTask(self):
        client = SSHClient.client(user=self.user)
        client.uploadFile(self.secretsJsonPath, Constants.SECRETS_JSON_PATH)
        client.uploadFile(self.googleServiceKeyPath, Constants.SECRETS_SERVICE_KEY_PATH)

class DeleteDevEnv(SetupTask):

    def __init__(self, user: str):
        self.user = user
    
    def name(self) -> str:
        return "Deleting Dev Environment"
    
    def runTask(self):
        client = SSHClient.client(user=self.user)
        client.execCommand(f"rm -f {Constants.DEV_ENV_PATH}")

class CreateProductionEnvFile(SetupTask):
    def __init__(self, user: str, dbUsername: str, dbPassword: str, djangoSecretKey: str):
        self.user = user
        self.dbUsername = dbUsername
        self.dbPassword = dbPassword
        self.djangoSecretKey = djangoSecretKey
    
    def name(self) -> str:
        return "Create Production Environment"
    
    def runTask(self):
        client = SSHClient.client(user=self.user)
        envFile = f"""
DEBUG=True
SECRET_KEY={self.djangoSecretKey}
DB_USER={self.dbUsername}
DB_PASSWORD={self.dbPassword}
"""
        logging.info("Writing Production Environment")
        result = client.execCommand(f"echo '{envFile}' > {Constants.PROD_ENV_PATH}")
        if not result.success():
            raiseCriticalException("Failed to create production environment")


class CreateAdminUser(SetupTask):
    def __init__(self, user:str, adminUser: str, adminPassword: str):
        self.user = user
        self.adminUser = adminUser
        self.adminPassword = adminPassword
    
    def name(self) -> str:
        return "Creating Admin user"
    
    def runTask(self):
        # Just run the command, if there already exists a user we can just report back since it will fail
        logging.info("Creating admin user %s",self.adminUser)
        client = SSHClient.client(user=self.user)

        result = client.execCommand(f"cd {Constants.DOCKER_DIR}")
        if not result.success():
            raiseCriticalException("Could not change into docker directory")
        
        result = client.execCommand(f"docker compose run -e DJANGO_SUPERUSER_PASSWORD={self.adminPassword} web python manage.py createsuperuser --noinput --username {self.adminUser}")
        if not result.success():
            if "username is already taken" in result.stderr or "username is already taken" in result.stdout:
                logging.info("Admin user already exists")
                return
            else:
                raiseCriticalException("Failed to create admin user")

# TODO: Need to figure out how to get nginx in a conatiner configured with SSL
# THat way we can keep all the config stuff within the container
# As for now just also install the requirements on main host
# Its not efficient but this is a refactor we can fix later

class RunWebsite(SetupTask):

    def __init__(self, user: str):
        self.user = user

    def name(self)->str:
        return "Running Website"

    def runTask(self):
        client = SSHClient.client(user=self.user)
        result = client.execCommand(f"cd {Constants.DOCKER_DIR}")
        if not result.success():
            raiseCriticalException("Failed to change to docker directory")
        
        logging.info("Installing Requirements for Django")
        # Need to create a venv to install the requirements
        result = client.execCommand(f"python3 -m venv {Constants.WORKING_DIR}/venv")
        if not result.success():
            raiseCriticalException("Failed to create python virtual environment")
        
        result = client.execCommand(f"{Constants.WORKING_DIR}/venv/bin/pip install -r {Constants.DOCKER_DIR}/requirements.txt")
        logging.info("Collecting static files")
        result = client.execCommand(f"{Constants.WORKING_DIR}/venv/bin/python {Constants.DOCKER_DIR}/manage.py collectstatic")
        if not result.success():
            raiseCriticalException("Failed to collect static")
        
        logging.info("Starting Containers")
        result = client.execCommand("docker compose up -d")
        if not result.success():
            raiseCriticalException("Failed to start docker containers")

class StopWebsite(SetupTask):

    def __init__(self, user: str):
        self.user = user

    def name(self)->str:
        return "Stop Website"

    def runTask(self):
        client = SSHClient.client(user=self.user)
        result = client.execCommand(f"cd {Constants.DOCKER_DIR}")
        if not result.success():
            raiseCriticalException("Failed to change to docker directory")
        
        result = client.execCommand("docker compose down")
        if not result.success():
            raiseCriticalException("Failed to stop docker containers")

#!SECTION

#SECTION - Driver

# https://realpython.com/django-nginx-gunicorn/
# https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/gunicorn/

# Have this script ssh into the droplet on behalf of the user

# Get config and secrets from arguments

# ssh/ftp in to set up initial config and secrets

# Configure lets encrypt

# Install Nginx
# Configure nginx to redirect to django
# Configure nginx to handle static files

# Run docker, point to the github for the docker compose file 

class Pipelines:

    @staticmethod
    def InitialDropletConfiguration(root:str, websiteUserPassword: str) -> list[SetupTask]:
        return [
        AptUpdateUpgrade(root=root),
        CreateWebsiteUser(root=root, userToCreate=Constants.WEBSITE_USER, userToCreatePassword=websiteUserPassword),
        CreateDirectories(user=Constants.WEBSITE_USER),
        InstallCoreUtils(root=root),
        InstallNginx(root=root), # NGinx is needed both for the webserver config and lets encyrpt, but Nginx config depends on let's encrypt so can't neatly put it anywhere else
        CloneRepo(user=Constants.WEBSITE_USER, cloneDir=Constants.CLONE_DIR, remote=Constants.AUSTIN_DSA_TOOLS_GITHUB_CLONE, gitDir=Constants.GIT_DIR)
    ]

    @staticmethod
    def updateRepoToVersion(version: str) -> list[SetupTask]:
        return [
            CheckoutToolsRepoVersion(user=Constants.WEBSITE_USER,gitDir=Constants.GIT_DIR,tagOrBranch=version)
        ]
    
    @staticmethod
    def SetupSSLLetsEncrypt(root: str, domain: str) -> list[SetupTask]:
        return [
            InstallSnap(root=root),
            InstallCertbot(root=root),
            LetsEncrypt(root=root,domain=domain)
        ]
    
    @staticmethod
    def ConfigureNginx(root:str, domain:str, regenerateDHCert: bool) -> list[SetupTask]:
        return [
            ConfigureNginx(root=root, websiteUser=Constants.WEBSITE_USER,domain=domain,errorLogPath=Constants.NGINX_ERROR_LOG, regenerateDHCert=regenerateDHCert),
            RestartNginx(root=root)
        ]
    
    @staticmethod
    def InstallDocker(root: str, userToAdd: str) -> list[SetupTask]:
        return [
            InstallDocker(root=root),
            AddUserToDockerGroup(root=root,userToAdd=userToAdd)
        ]

    @staticmethod
    def DeployWebsite(secretsJsonPath: str, googleServiceKeyPath: str, adminUser: str, adminPassword: str, version: str) -> list[SetupTask]:
        return [
            FetchRepo(Constants.WEBSITE_USER, Constants.GIT_DIR),
            CheckoutToolsRepoVersion(user=Constants.WEBSITE_USER,gitDir=Constants.GIT_DIR,tagOrBranch=version),
            PullRepo(user=Constants.WEBSITE_USER, gitDir=Constants.GIT_DIR),
            OverwriteRepoToWorkingDirectory(user=Constants.WEBSITE_USER,gitDir=Constants.GIT_DIR, workingDir=Constants.WORKING_DIR),
            CopyOverWebsiteSecrets(user=Constants.WEBSITE_USER, secretsJsonPath=secretsJsonPath, googleServiceKeyPath=googleServiceKeyPath),
            DeleteDevEnv(user=Constants.WEBSITE_USER),
            # Start and stop so the container will be built and migrated but then stop so we can add admin user
            RunWebsite(user=Constants.WEBSITE_USER),
            StopWebsite(user=Constants.WEBSITE_USER),
            CreateAdminUser(user=Constants.WEBSITE_USER, adminUser=adminUser, adminPassword=adminPassword)
        ]

def deploy(flags: Flags):
    # Register Potential SSH users
    SSHClient.registerClient(SSHClientEntry(user=flags.rootUser, password=flags.rootPassword, isRoot=True, ip=flags.sshIp, port=flags.sshPort, client=None))
    SSHClient.registerClient(SSHClientEntry(user=Constants.WEBSITE_USER, password=flags.websitePassword, isRoot=False, ip=flags.sshIp, port=flags.sshPort, client=None))
    tasksToRun : list[SetupTask] = []
    # Eventually will build this based on mode
    tasksToRun.extend(Pipelines.InitialDropletConfiguration(root=flags.rootUser, websiteUserPassword=flags.websitePassword))
    tasksToRun.extend(Pipelines.updateRepoToVersion(version=flags.websiteToolsVersion))
    tasksToRun.extend(Pipelines.SetupSSLLetsEncrypt(root=flags.rootUser,domain=flags.websiteDomain))
    tasksToRun.extend(Pipelines.ConfigureNginx(root=flags.rootUser, domain=flags.websiteDomain, regenerateDHCert=flags.regenerateDHCert))
    tasksToRun.extend(Pipelines.InstallDocker(root=flags.rootUser, userToAdd=Constants.WEBSITE_USER))
    tasksToRun.extend(Pipelines.DeployWebsite(secretsJsonPath=flags.secretsJsonPath, googleServiceKeyPath=flags.googleServiceKeyPath, adminUser=flags.adminUsername, adminPassword=flags.adminPassword, version=flags.websiteToolsVersion))
    

    for task in tasksToRun:
        try:
            logging.info("Beginning task %s", task.name())
            task.runTask()
            logging.info("Finished task %s", task.name())
        except Exception as e:
            logging.error("Failed setup on task %s", task.name())
            logging.exception(e)
            exit(1)
    
    # Cleanup
    logging.info("Closing ssh connection")
    SSHClient.closeClients()


if __name__ == "__main__":
    flags = Flags.parseFlags()
    deploy(flags)

#!SECTION