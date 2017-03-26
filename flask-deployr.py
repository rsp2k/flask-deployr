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

import venv

from flask import Flask, request, abort, jsonify

from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.hybrid import hybrid_property

CHECKOUT_BASE = '/home/deploy'
VASSALS_DIR = '/etc/uwsgi/vassals'
REPO_URL = 'https://github.com/%s.git'
API_IPS_URL = 'https://api.github.com/meta'
GIT = '/usr/bin/git'




import configparser

def valid_signature(key, body, signature):
    """
    Validate signature is valid for body using key
    """

    signature_parts = signature.split('=')
    if signature_parts[0] != "sha1":
        return False
    generated_sig = hmac.new(str.encode(key), msg=body, digestmod=sha1)
    return hmac.compare_digest(generated_sig.hexdigest(), signature_parts[1])

def check_request_ip(request):
    request_ip = ipaddress.ip_address(u'{0}'.format(request.remote_addr))
    for block in requests.get(API_IPS_URL).json()['hooks']:
        if ipaddress.ip_address(request_ip) in ipaddress.ip_network(block):
            break
    else:
        abort(401)

class ConfigClass(object):
    SECRET_KEY = os.getenv('SECRET_KEY', 'THIS IS AN INSECURE SECRET')
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
    repo_name = db.Column(db.String(255), unique=True)
    secret = db.Column(db.String(255), nullable=False, server_default='')
    environment_variables = db.relationship('EnvironmentVar', backref='application', lazy='dynamic')

    def __repr__(self):
        return self.name

    @hybrid_property
    def repo_url(self):
        return REPO_URL % self.repo_name

    @hybrid_property
    def path(self):
        return os.path.join(CHECKOUT_BASE, self.repo_name)

    @hybrid_property
    def name(self):
        if '/' in self.repo_name:
           return self.repo_name.split('/')[1]
        return self.repo_name

    def git_clone(self):
        """
        Perform initial clone from application.repo_url
        """
        if (os.path.exists(self.path) and os.path.isdir(self.path)):
            raise Exception('Initial clone already exists, update instead')

        parent_dir = os.path.dirname(self.path)
        command = [GIT, 'clone', self.repo_url]
        subp = subprocess.Popen(command, cwd=parent_dir)
        subp.wait()

    def git_pull(self):
        """
        Pull application
        """
        if not (os.path.exists(self.path) and os.path.isdir(self.path)):
            raise Exception('Repo path %s does not exist' % self.path)

        command = [GIT, 'pull']
        subp = subprocess.Popen(command, cwd=self.path)
        subp.wait()


    @hybrid_property
    def envdir_path(self):
        return os.path.join(self.path, 'envdir')

    def envdir(self):
        """
        Create envdir based on list of env_vars
        """

        envdir_path = self.envdir_path
        if not (os.path.exists(self.envdir_path) and os.path.isdir(self.envdir_path)):
            os.makedirs(self.envdir_path)

        for var in self.environment_variables.all():
            with open(os.path.join(self.envdir_path, var.name), 'w') as f:
                f.write(var.value)

    @hybrid_property
    def venv_path(self):
        return os.path.join(self.path, '.venv')

    def venv_destroy(self):
        """
        remove venv directory
        """
        if (os.path.exists(self.venv_path) and os.path.isdir(self.venv_path)):
           import shutil
           shutil.rmtree(self.venv_path)

    def venv_create(self):
        """
        Create python virtual environment if necessary
    
        https://docs.python.org/3/library/venv.html
        """
        if not (os.path.exists(self.venv_path) and os.path.isdir(self.venv_path)):
            builder = venv.EnvBuilder()
            builder.create(self.venv_path)
            return True

        return False

    def pip_install_requirements(self):
        """
        Pip install from requirements.txt if file exits
        """
        requirements_file = os.path.join(self.path, 'requirements.txt')
        if os.path.isfile(requirements_file):
            command = ['pip', 'install', '-r', kwargs['requirements_file'],]
            subp = subprocess.Popen(command, cwd=self.path)
            subp.wait()


    @hybrid_property
    def uwsgi_config_path(self):
        return os.path.join(self.path, "%s.ini" % self.name)

    @hybrid_property
    def uwsgi_config(self):
        """
        Return dictionary of uwsgi config
        """
        logger_path = "file:%s/errlog" % self.path 
        reqlog_path = "file:%s/reqlog" % self.path 

        return {
            'virtualenv': self.venv_path,
            'chdir': self.path,
            'envdir': self.envdir_path,
            'module': '%n:app',

            'socket': '/tmp/%n.sock',
            'chmod-socket': '777',

            'plugins': 'python3,logfile',
            'python-autoreload': '3',
    
            'logger': logger_path,
            'req-logger': reqlog_path,
        }


    def uwsgi_write_config(self):
        """
        Write uwsgi config file for application
        """
        config = configparser.ConfigParser(interpolation=None)
        config['uwsgi'] = self.uwsgi_config

        print(self.uwsgi_config_path)

        with open(self.uwsgi_config_path, "w") as config_file:
            config.write(config_file)
 

    def uwsgi_restart(self):
        """
        Restart the uwsgi server
        """

        dst = os.path.join(VASSALS_DIR, os.path.basename(self.uwsgi_config_path))

        if (os.path.exists(dst) and os.path.islink(dst)):
            from pathlib import Path
            Path(self.uwsgi_config_path).touch()

        elif (os.path.exists(VASSALS_DIR) and os.path.isdir(VASSALS_DIR)):
            if not os.path.islink(dst):
                os.symlink(self.uwsgi_config_path, dst)


class ApplicationAdmin(ModelView):
    column_display_pk = True
    form_columns = ['repo_name', 'secret']
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
        application = Application.query.filter_by(repo_name=repo_name).first()

    # Fallback to plain owner/name lookup
    if not application:
        repo_name = '{owner}/{name}'.format(**repo_meta)
        application = Application.query.filter_by(repo_name=repo_name).first()
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
        application.git_clone()
        application.venv_create()
        application.uwsgi_write_config()
    else:
        application.git_pull()
        application.uwsgi_restart()

    application.envdir()

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
