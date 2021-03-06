# flask-deployr

Provides and admin page and GitHub webhook receiver. When it receives a "commit" webhook message, either due to a remote push, or the repo being edited, it pulls down the changes. Secret and/or deployement specific info is managed in envrionment variables that can be edited via the web.

Currently only python / pip are supported, node.js / npm will be added in the future. Pull requests welcome!

Attempts have been made at using venv and virtualenv modules to 

## uWSGI Emperor Mode 'vassal' support
A uWSGI vassal configuration file is created each time the webhook fires or the app is manually updated (from web admin page). This causes uWSGI to restart the app gracefully.

The uWSGI file created will create a socket named '/tmp/**app_name**/.sock'

## NGINX Example Config
The example nginx config uses whatever hostname was supplied and attempts to connect to a like named socket named '/tmp/**hostname**.sock'. Requests to the '/static/' prefix are sent to /home/depoy/**hostname**/static

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes. See deployment for notes on how to deploy the project on a live system.

### Prerequisites

What things you need to install the software and how to install them

```
Give examples
```

### Installing

A step by step series of examples that tell you have to get a development env running

Say what the step will be

```
Give the example
```

And repeat

```
until finished
```

End with an example of getting some data out of the system or using it for a little demo

## Running the tests

Explain how to run the automated tests for this system

### Break down into end to end tests

Explain what these tests test and why

```
Give an example
```

### And coding style tests

Explain what these tests test and why

```
Give an example
```

## Deployment

Setup uWSGI w/Emperor Support, set the VASSALS_DIR

## Repository Webhook
Setup a repository "commit" webhook that points to the URL that serves this app (/webhook)

## Built With

* [Flask](http://flask.pocoo.org/) - The web framework used
* [Flask-Admin](http://flask.pocoo.org/) - CRUD/Admin page
* [uWSGI](http://uwsgi-docs.readthedocs.io/) - uWSGI

## Contributing

Please submit pull requests to us!

## Versioning



## Authors

* **Ryan Malloy** - *Initial work* - [rsp2k](https://github.com/rsp2k)

See also the list of [contributors](https://github.com/rsp2k/deployr/contributors) who participated in this project.

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details

## Acknowledgments

* Thanks to everyone on the Internet that helped with this
