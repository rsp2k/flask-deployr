#!/usr/bin/env python
# -*- coding: utf-8 -*-

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

import logging
from logging.handlers import RotatingFileHandler

from flask import Flask, request, abort, jsonify

from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView

from flask_sqlalchemy import SQLAlchemy

CHECKOUT_BASE = '/home/deploy'
REPO_URL = 'https://github.com/%s.git'
API_IPS_URL = 'https://api.github.com/meta'
GIT = '/usr/bin/git'

def nginx_config(application, path):
    pass

def envdir(path, env_vars):
    """
    Create envdir at path based on list of env_vars
    """

    envdir_path = os.path.join(path, 'envdir')
    if not (os.path.exists(envdir_path) and os.path.isdir(envdir_path)):
        os.makedirs(envdir_path)

    for var in env_vars:
        with open(os.path.join(envdir_path, var.name), 'w') as f:
            f.write(var.value)


def initial_clone(repo, path):
    full_repo = REPO_URL % repo
    command = [GIT, 'clone', full_repo]
    parent_dir = os.path.dirname(path)
    subp = subprocess.Popen(command, cwd=parent_dir)
    subp.wait()

def update_repo(path):
    command = [GIT, 'pull']
    subp = subprocess.Popen(command, cwd=path)
    subp.wait()

def valid_signature(key, body, signature):
    """
    Validate signature is valid for body using key
    """

    signature_parts = signature.split('=')
    if signature_parts[0] != "sha1":
        return False
    generated_sig = hmac.new(str.encode(key), msg=body, digestmod=sha1)
    return hmac.compare_digest(generated_sig.hexdigest(), signature_parts[1])

def hook_ip_blocks():
    """
    Return list of valid source IP's
    """

def check_request_ip(request):
    return
    request_ip = ipaddress.ip_address(u'{0}'.format(request.remote_addr))
    for block in requests.get(API_IPS_URL).json()['hooks']:
        if ipaddress.ip_address(request_ip) in ipaddress.ip_network(block):
            break
    else:
        abort(401)

class ConfigClass(object):
#    SECRET_KEY = os.getenv('SECRET_KEY', 'THIS IS AN INSECURE SECRET')
    DEBUG = os.getenv('DEBUG', True)
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///db.sqlite')
    SQLALCHEMY_TRACK_MODIFICATIONS = False


# Setup Flask app and app.config
app = Flask(__name__)
app.config.from_object(__name__+'.ConfigClass')

# FIXME
app.debug = True

db = SQLAlchemy(app)

# Models
class EnvironmentVar(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255),unique=True)
    value = db.Column(db.String(500), nullable=False, server_default='')
    applicaton = db.Column(db.String(255), db.ForeignKey('application.id'))

    def __repr__(self):
        return "%s %s" % (self.application.name, self.name)

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True)
    secret = db.Column(db.String(255), nullable=False, server_default='')
    environment_variables = db.relationship('EnvironmentVar', backref='application', lazy='dynamic')

    def __repr__(self):
        return self.name


class ApplicationAdmin(ModelView):
    column_display_pk = True
    form_columns = ['name', 'secret']
    inline_models = (EnvironmentVar,)


app.config.update({
   'KONCH_CONTEXT': {
      'db': db,
      'Application': Application,
   },
   'KONCH_PTPY_VI_MODE': True
})

# Create all database tables
db.create_all()

admin = Admin(app, name=__name__, template_mode='bootstrap3')
admin.add_view(ApplicationAdmin(Application, db.session))

@app.route("/webhook", methods=['GET', 'POST'])
def webhook():
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

    application = None
    match = re.match(r"refs/heads/(?P<branch>.*)", payload['ref'])
    if match:
        repo_meta['branch'] = match.groupdict()['branch']
        repo_name = '{owner}/{name}/branch:{branch}'.format(**repo_meta)
        application = Application.query.filter_by(name=repo_name).first()

    # Fallback to plain owner/name lookup
    if not application:
        repo_name = '{owner}/{name}'.format(**repo_meta)
        application = Application.query.filter_by(name=repo_name).first()
        if not application: abort(404)

        if application.secret:
            signature = request.headers.get('X-Hub-Signature')
            if not valid_signature(application.secret, request.data, signature):
                abort(400)

        owner_root = os.path.join(CHECKOUT_BASE, repo_meta['owner'])
        if not (os.path.exists(owner_root) and os.path.isdir(owner_root)):
            os.makedirs(owner_root)

        path = os.path.join(owner_root, repo_meta['name'])
        if not (os.path.exists(path) and os.path.isdir(path)):
            initial_clone(repo_name, path)
        else:
            update_repo(path)

        envdir(path, application.environment_variables.all())

        """
        actions = repo.get('action', None)
        if actions:
            for action in actions:
                subp = subprocess.Popen(action, cwd=path)
                subp.wait()

        """

        return 'OK'

if __name__ == "__main__":
    app.run()
