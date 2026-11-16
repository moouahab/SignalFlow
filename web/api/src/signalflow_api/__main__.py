"""Commande locale avec écoute exclusivement sur la boucle locale."""
import argparse


def main() -> None:
    """Lancer Uvicorn sans confiance dans les en-têtes de proxy."""
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('le port doit être compris entre 1 et 65535')
    uvicorn.run('signalflow_api.app:create_app', factory=True, host='127.0.0.1',
                port=args.port, proxy_headers=False, access_log=False)


if __name__ == '__main__':
    main()
