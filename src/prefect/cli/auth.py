import click
import warnings
from click.exceptions import Abort
from tabulate import tabulate

from prefect import Client, config
from prefect.utilities.exceptions import AuthorizationError, ClientError
from prefect.cli.build_register import handle_terminal_error, TerminalError


def check_override_auth_token():
    if config.cloud.get("auth_token"):
        click.secho("Auth token already present in config.", fg="red")
        raise Abort


def abort_on_config_api_key(message: str = None):
    if config.cloud.get("api_key"):
        # Add a leading space if not null
        message = (" " + message) if message else ""
        raise TerminalError(
            "Your API key is set in the Prefect config instead of with the CLI."
            + message
        )


@click.group(hidden=True)
def auth():
    """
    Handle Prefect Cloud authorization.

    \b
    Usage:
        $ prefect auth [COMMAND]

    \b
    Arguments:
        login           Log in to Prefect Cloud
        logout          Log out of Prefect Cloud
        list-tenants    List your available tenants
        switch-tenants  Switch to a different tenant
        create-token    Create an API token (DEPRECATED)
        list-tokens     List the names and ids of existing API tokens (DEPRECATED)
        revoke-token    Remove an API token from the backend (DEPRECATED)

    \b
    Examples:
        $ prefect auth login --token MY_TOKEN
        Login successful!

    \b
        $ prefect auth logout
        Logged out from tenant TENANT_ID

    \b
        $ prefect auth list-tenants
        NAME                        SLUG                        ID
        Test Person                 test-person                 816sghf2-4d51-4338-a333-1771gns7614d
        test@prefect.io's Account   test-prefect-io-s-account   \
1971hs9f-e8ha-4a33-8c33-64512gds86g1  *

    \b
        $ prefect auth switch-tenants --slug test-person
        Tenant switched

    \b
        $ prefect auth create-token -n MyToken -s RUNNER
        ...token output...
    """
    if config.backend == "server":
        raise click.UsageError("Auth commands with server are not currently supported.")


@auth.command(hidden=True)
@click.option(
    "--key",
    "-k",
    help="A Prefect Cloud API key.",
)
@click.option(
    "--token",
    "-t",
    help="A Prefect Cloud API token. DEPRECATED.",
)
@handle_terminal_error
def login(key, token):
    """
    Log-in to Prefect Cloud with an API key

    You will be switched to the default tenant associated with the key.
    """
    if not key and not token:
        raise TerminalError("You must supply an API key or token!")

    if key and token:
        raise TerminalError("You cannot supply both an API key and token")

    abort_on_config_api_key(
        "To log in with the CLI, remove the config key `prefect.cloud.api_key`"
    )

    try:
        # Attempt to treat the input like an API key even if it is passed as a token
        client = Client(api_key=key or token)
    except AuthorizationError:
        if key:  # We'll catch an error again later if using a token
            raise TerminalError("Unauthorized. Invalid Prefect Cloud API key.")
    except ClientError:
        raise TerminalError("Error attempting to communicate with Prefect Cloud.")
    else:
        client._write_auth_to_disk()

    # Backwards compatibility for tokens
    if token:
        check_override_auth_token()
        client = Client(api_token=token)

        # Verify login obtained a valid api token
        try:
            output = client.graphql(
                query={"query": {"user": {"default_membership": "tenant_id"}}}
            )

            # Log into default membership
            success_login = client.login_to_tenant(
                tenant_id=output.data.user[0].default_membership.tenant_id
            )

            if not success_login:
                raise AuthorizationError

        except AuthorizationError:
            click.secho(
                f"Error attempting to use Prefect API token {token}. "
                "Please check that you are providing a USER scoped Personal Access Token.\n"
                "For more information visit the documentation for USER tokens at "
                "https://docs.prefect.io/orchestration/concepts/tokens.html#user",
                fg="red",
            )
            return
        except ClientError:
            click.secho(
                "Error attempting to communicate with Prefect Cloud. "
                "Please check that you are providing a USER scoped Personal Access Token.\n"
                "For more information visit the documentation for USER tokens at "
                "https://docs.prefect.io/orchestration/concepts/tokens.html#user",
                fg="red",
            )
            return

        # save token
        client.save_api_token()

    click.secho("Login successful!", fg="green")


@auth.command(hidden=True)
@handle_terminal_error
def logout():
    """
    Log out of Prefect Cloud
    """

    client = Client()

    if client.api_key:

        # Check the source of the API key
        abort_on_config_api_key(
            "To log out, remove the config key `prefect.cloud.api_key`"
        )

        click.confirm(
            "Are you sure you want to log out of Prefect Cloud? "
            "This will remove your API key from this machine.",
            default=False,
            abort=True,
        )

        # Clear the key and tenant id then write to the cache
        client.api_key = ""
        client._tenant_id = ""
        client._write_auth_to_disk()

        click.secho(f"Logged out of Prefect Cloud", fg="green")

    elif client._api_token:

        check_override_auth_token()
        tenant_id = client.active_tenant_id

        if not tenant_id:
            click.confirm(
                "Are you sure you want to log out of Prefect Cloud? "
                "This will remove your API token from this machine.",
                default=False,
                abort=True,
            )

            # Remove the token from local storage by writing blank settings
            client._save_local_settings({})
            click.secho(f"Logged out of Prefect Cloud", fg="green")

        else:
            # Log out of the current tenant (dropping the access token) while retaining
            # the API token. This is backwards compatible behavior. Running the logout
            # command twice will remove the token from storage entirely
            click.confirm(
                "Are you sure you want to log out of your current Prefect Cloud tenant?",
                default=False,
                abort=True,
            )

            client.logout_from_tenant()

            click.secho("Logged out from tenant {}".format(tenant_id), fg="green")

    else:
        raise TerminalError(
            "You are not logged in to Prefect Cloud. "
            "Use `prefect auth login` to log in first."
        )


@auth.command(hidden=True)
def list_tenants():
    """
    List available tenants
    """
    check_override_auth_token()

    client = Client()

    tenants = client.get_available_tenants()

    output = []
    for item in tenants:
        active = None
        if item.id == client.tenant_id:
            active = "*"
        output.append([item.name, item.slug, item.id, active])

    click.echo(
        tabulate(
            output,
            headers=["NAME", "SLUG", "ID", ""],
            tablefmt="plain",
            numalign="left",
            stralign="left",
        )
    )


@auth.command(hidden=True)
@click.option(
    "--id", "-i", required=False, help="A Prefect Cloud tenant id.", hidden=True
)
@click.option(
    "--slug", "-s", required=False, help="A Prefect Cloud tenant slug.", hidden=True
)
def switch_tenants(id, slug):
    """
    Switch active tenant

    \b
    Options:
        --id, -i    TEXT    A Prefect Cloud tenant id
        --slug, -s  TEXT    A Prefect Cloud tenant slug
    """

    # If the config specifies a tenant explicitly, it is used before this mechanism
    if config.cloud.get("tenant_id"):
        click.secho(
            "Your tenant id has been set in the Prefect config instead of with the "
            "CLI.  To switch tenants with the CLI, remove the config key "
            " `prefect.cloud.tenant_id`",
            fg="red",
        )
        return

    # Deprecated API token check
    check_override_auth_token()

    client = Client()

    login_success = client.login_to_tenant(tenant_slug=slug, tenant_id=id)
    if not login_success:
        click.secho("Unable to switch tenant", fg="red")
        return
    else:
        client._write_auth_to_disk()

    click.secho(f"Tenant switched to {client.tenant_id}", fg="green")


@auth.command(hidden=True)
@click.option("--name", "-n", required=True, help="A token name.", hidden=True)
@click.option("--scope", "-s", required=True, help="A token scopre.", hidden=True)
def create_token(name, scope):
    """
    DEPRECATED. Please use API keys instead.

    Create a Prefect Cloud API token.

    For more info on API tokens visit https://docs.prefect.io/orchestration/concepts/api.html

    \b
    Options:
        --name, -n      TEXT    A name to give the generated token
        --scope, -s     TEXT    A scope for the token
    """
    click.secho(
        "WARNING: API tokens are deprecated. Please use `prefect auth create-key` to "
        "create an API key instead.",
        fg="yellow",
        err=True,  # Write to stderr in case the user is piping
    )

    client = Client()

    output = client.graphql(
        query={
            "mutation($input: create_api_token_input!)": {
                "create_api_token(input: $input)": {"token"}
            }
        },
        variables=dict(input=dict(name=name, scope=scope)),
    )

    if not output.get("data", None):
        click.secho("Issue creating API token", fg="red")
        return

    click.echo(output.data.create_api_token.token)


@auth.command(hidden=True)
def list_tokens():
    """
    DEPRECATED. Please use API keys instead.

    List your available Prefect Cloud API tokens.
    """
    click.secho(
        "WARNING: API tokens are deprecated. Please consider removing your remaining "
        "tokens and using API keys instead.",
        fg="yellow",
        err=True,  # Write to stderr in case the user is piping
    )

    client = Client()

    output = client.graphql(query={"query": {"api_token": {"id", "name"}}})

    if not output.get("data", None):
        click.secho("Unable to list API tokens", fg="red")
        return

    tokens = []
    for item in output.data.api_token:
        tokens.append([item.name, item.id])

    click.echo(
        tabulate(
            tokens,
            headers=["NAME", "ID"],
            tablefmt="plain",
            numalign="left",
            stralign="left",
        )
    )


@auth.command(hidden=True)
@click.option("--id", "-i", required=True, help="A token ID.", hidden=True)
def revoke_token(id):
    """
    DEPRECATED. Please use API keys instead.

    Revote a Prefect Cloud API token

    \b
    Options:
        --id, -i    TEXT    The id of a token to revoke
    """
    check_override_auth_token()

    client = Client()

    output = client.graphql(
        query={
            "mutation($input: delete_api_token_input!)": {
                "delete_api_token(input: $input)": {"success"}
            }
        },
        variables=dict(input=dict(token_id=id)),
    )

    if not output.get("data", None) or not output.data.delete_api_token.success:
        click.secho("Unable to revoke token with ID {}".format(id), fg="red")
        return

    click.secho("Token successfully revoked", fg="green")
