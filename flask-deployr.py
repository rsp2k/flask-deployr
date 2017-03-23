#!/usr/bin/env python
import io
import os
import re
import sys
import subprocess
import json
import requests
import ipaddress
import hmac
from hashlib import sha1
from flask import Flask, request, abort, jsonify

import logging
from logging.handlers import RotatingFileHandler

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GIT = '/usr/bin/git'

app = Flask(__name__)
app.debug = True #os.environ.get('DEBUG') == 'true'


# The repos.json file should be readable by the user running the Flask app,
# and the absolute path should be given by this environment variable.
try:
    REPOS_JSON_PATH = os.environ['REPOS_JSON_PATH']
except:
    REPOS_JSON_PATH = os.path.join(BASE_DIR, 'repos.json')

REPOS = json.loads(io.open(REPOS_JSON_PATH, 'r').read())

def valid_signature(key, body, signature):
    signature_parts = signature.split('=')
    if signature_parts[0] != "sha1":
        return False
    generated_sig = hmac.new(str.encode(key), msg=body, digestmod=sha1)
    return hmac.compare_digest(generated_sig.hexdigest(), signature_parts[1])

def hook_ip_blocks():
    return requests.get('https://api.github.com/meta').json()['hooks']

def check_request_ip(request):
    request_ip = ipaddress.ip_address(u'{0}'.format(request.remote_addr))
    # Check if the POST request is from github.com or GHE
    for block in hook_ip_blocks():
        if ipaddress.ip_address(request_ip) in ipaddress.ip_network(block):
            break  # the remote_addr is within the network range of github.
    else:
        abort(401)

def initial_clone(repo, path):
    full_repo = 'https://github.com/%s.git' % repo
    command = [GIT, 'clone', full_repo]
    parent_dir = os.path.dirname(path)
    subp = subprocess.Popen(command, cwd=parent_dir)
    subp.wait()


def update_repo(path):
    command = [GIT, 'pull']
    subp = subprocess.Popen(command, cwd=path)
    subp.wait()


@app.route("/", methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        return 'OK'
    elif request.method == 'POST':
        check_request_ip(request)

        if request.headers.get('X-GitHub-Event') == "ping":
            return jsonify({'msg': 'Hi!'})
        if request.headers.get('X-GitHub-Event') != "push":
            return jsonify({'msg': "wrong event type"})

        payload = request.get_json()

        repo_meta = {
            'name': payload['repository']['name'],
            'owner': payload['repository']['owner']['name'],
        }

        # Try to match on branch as configured in repos.json
        match = re.match(r"refs/heads/(?P<branch>.*)", payload['ref'])
        if match:
            repo_meta['branch'] = match.groupdict()['branch']
            repo_name = '{owner}/{name}/branch:{branch}'.format(**repo_meta)
            repo = REPOS.get(repo_name, None)

            # Fallback to plain owner/name lookup
            if not repo:
                repo_name = '{owner}/{name}'.format(**repo_meta)
                repo = REPOS.get(repo_name, None)
                if not repo: abort(404)

        key = repo.get('key', None)
        if key:
            signature = request.headers.get('X-Hub-Signature')
            if not valid_signature(key, request.data, signature): abort(400)

        path = repo.get('path', None)
        if path:
            if not (os.path.exists(path) and os.path.isdir(path)):
                initial_clone(repo_name, path)
            else:
                update_repo(path)

        actions = repo.get('action', None)
        if actions:
            for action in actions:
                subp = subprocess.Popen(action, cwd=repo.get('path', '.'))
                subp.wait()
        return 'OK'

if __name__ == "__main__":
    app.run()
