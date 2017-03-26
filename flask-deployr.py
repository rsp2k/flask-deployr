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
import shutil
from hashlib import sha1

import logging
from logging.handlers import RotatingFileHandler

import venv
import configparser

from flask import Flask, request, abort, jsonify

from flask_admin import Admin
from flask_admin.actions import action
from flask_admin.contrib.sqla import ModelView

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.hybrid import hybrid_property

CHECKOUT_BASE = '/home/deploy'
VASSALS_DIR = '/etc/uwsgi/vassals'

GITHUB_API_SERVERS = requests.get('https://api.github.com/meta').json()['hooks']
GIT = '/usr/bin/git'

def valid_request_signature(key, request):
    """
    Validate request was signed with key
    """

    signature = request.headers.get('X-Hub-Signature')
    signature_parts = signature.split('=')
    if signature_parts[0] != "sha1":
        return False
    generated_sig = hmac.new(str.encode(key), msg=request.data, digestmod=sha1)
    return hmac.compare_digest(generated_sig.hexdigest(), signature_parts[1])

def check_request_ip(request):
    request_ip = ipaddress.ip_address(u'{0}'.format(request.remote_addr))
    for block in GITHUB_API_SERVERS:
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
        return "%s %s = %s" % (self.application.name, self.name, self.value)

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(255), unique=True, nullable=False)
    repo_url = db.Column(db.String(255), unique=True, nullable=False)
    webhook_secret = db.Column(db.String(255), nullable=False, server_default='')
    environment_variables = db.relationship('EnvironmentVar', backref='application', lazy='dynamic')

    def __repr__(self):
        return '<a href="%s">%s</a>' % (self.repo_url, self.name)

    @hybrid_property
    def path(self):
        return os.path.join(CHECKOUT_BASE, self.name)

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
            command = ['pip', 'install', '-r', requirements_file,]
            subp = subprocess.Popen(command, cwd=self.path)
            subp.wait()


    @hybrid_property
    def uwsgi_config_path(self):
        return os.path.join(self.path, "%s.ini" % self.name)

    @hybrid_property
    def uwsgi_vassal_symlink(self):
       return os.path.join(VASSALS_DIR, os.path.basename(self.uwsgi_config_path))

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
#            'python-autoreload': '3',
    
            'logger': logger_path,
            'req-logger': reqlog_path,
        }


    def uwsgi_write_config(self):
        """
        Write uwsgi config file for application
        """

        self.envdir()

        config = configparser.ConfigParser(interpolation=None)
        config['uwsgi'] = self.uwsgi_config

        with open(self.uwsgi_config_path, "w") as config_file:
            config.write(config_file)
 

    def start(self):
        """
        Start the uwsgi site for this applicaton
        """

        self.uwsgi_write_config()

        if (os.path.exists(VASSALS_DIR) and os.path.isdir(VASSALS_DIR)):
            dst = self.uwsgi_vassal_symlink
            if os.path.islink(dst):
                os.unlink(dst)
            os.symlink(self.uwsgi_config_path, dst)
            return True

        raise Exception('Unable to write to vassals directory: %s' % dst)

    def stop(self):
        """
        Stop the uwsgi site for this application
        """

        if os.path.islink(self.uwsgi_vassal_symlink):
            os.unlink(self.uwsgi_vassal_symlink)
            return True
        return False

    def restart(self):
        self.stop()
        self.start()

    def delete_all_application_files(self):
        """
        Deletes files when application is deleted
        """

        self.stop()
        if os.path.exists(self.path):
            shutil.rmtree(self.path)

        return True


class ApplicationAdmin(ModelView):
#    column_display_pk = True
#    form_columns = ['repo_url', 'name', 'webhook_secret']
    inline_models = (EnvironmentVar,)

    def after_model_delete(self, model):
        model.delete_all_application_files()

    def after_model_change(self, form, model, is_created):
        if is_created:
            model.git_clone()
            model.venv_create()
        else:
            model.git_pull()
 
        model.restart()

    @action('pull', 'Pull', 'Are you sure you want to fetch/merge the selected applications?')
    def action_pull(self, ids):
        for id_ in ids:
            a = Application.query.get(id_)
            a.git_pull()

    @action('restart', 'Restart', 'Are you sure you want to restart the selected applications?')
    def action_restart(self, ids):
        for id_ in ids:
            a = Application.query.get(id_)
            a.restart()


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

    repo_name = payload['repository']['name']

    if repo_name is __name__:
        return jsonify({'msg': "cant deploy repo named %s" % __name__})

    application = Application.query.filter_by(name=repo_name).first()
    if not application: abort(404)

    if application.webhook_secret:
        if not valid_request_signature(application.webhook_secret, request):
            abort(400)

    path = os.path.join(CHECKOUT_BASE, application.name)
    if not (os.path.exists(path) and os.path.isdir(path)):
        application.git_clone()
        application.venv_create()
    else:
        application.git_pull()

    application.pip_install_requirements()
    application.restart()

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
