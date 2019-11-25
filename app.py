import json
import os
import zipfile
import tempfile
import traceback
import shutil

from flask import (Flask, Response, flash, redirect, render_template, request,
    send_from_directory, send_file, session, url_for, after_this_request, flash)
import logging
from logging.handlers import RotatingFileHandler
from flask_security import (Security, SQLAlchemyUserDatastore, login_required,
    roles_required, current_user)
from flask_security.utils import hash_password
from sqlalchemy.sql.expression import func, select
from werkzeug import secure_filename
from db import (create_tokens, insert_collection,
    newest_collections)
from filters import format_date
from forms import (BulkTokenForm, CollectionForm, ExtendedLoginForm,
    ExtendedRegisterForm, UserEditForm, RoleForm)
from models import Collection, Recording, Role, Token, User, Session, db
from flask_reverse_proxy_fix.middleware import ReverseProxyPrefixFix
from ListPagination import ListPagination

# initialize the logger
logHandler = RotatingFileHandler('logs/info.log', maxBytes=1000,
    backupCount=1)
logHandler.setLevel(logging.DEBUG)
logHandler.setFormatter(logging.Formatter(
    '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
))


app = Flask(__name__)
app.config.from_pyfile('{}.py'.format(os.path.join('settings/',
    os.getenv('FLASK_ENV', 'development'))))
app.logger.setLevel(logging.DEBUG)
app.logger.addHandler(logHandler)

if 'REVERSE_PROXY_PATH' in app.config:
    ReverseProxyPrefixFix(app)

db.init_app(app)
user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore, login_form=ExtendedLoginForm)

# register filters
app.jinja_env.filters['datetime'] = format_date

SESSION_SZ = 50

# GENERAL ROUTES
@app.route('/')
@login_required
def index():
    app.logger.info(f"{current_user} Requesting index")
    return render_template('index.jinja', collections=newest_collections(num=4))

@app.route('/lobe/')
@login_required
def index_redirect():
    return redirect(url_for('index'))

@app.route('/post_recording/', methods=['POST'])
@login_required
def post_recording():
    app.logger.info(f"{current_user} Posting new recordings")
    recordings = []
    files = []
    duration = None
    record_session = None
    try:
        for token_id in request.form:
            item = json.loads(request.form[token_id])
            # HACK that should be changed
            if token_id == 'duration':
                duration = float(item)
            elif item == 'skipped':
                token = Token.query.get(int(token_id))
                token.marked_as_bad = True
            else:
                transcription = item['transcript']
                file_obj = request.files.get('file_{}'.format(token_id))
                recording = Recording(token_id, file_obj.filename, session['user_id'],
                    transcription)
                db.session.add(recording)
                recordings.append(recording)
                files.append(file_obj)
        db.session.commit()
        # only create session if at least one recording
        if len(recordings) > 0:
            collection_id = recordings[0].get_collection()
            record_session = Session(session['user_id'], collection_id,
                duration=duration)
            db.session.add(record_session)
            for idx, recording in enumerate(recordings):
                recording.save_to_disk(files[idx])
                recording.set_wave_params()
                recording.set_session_id(record_session.id)
        db.session.commit()
    except Exception as error:
        app.logger.error(f"Error posting recordings: {error}\n{traceback.format_exc()}")
        return Response(str(error), status=500)

    if record_session is None:
        return Response(url_for('index'), status=200)
    else:
        return Response(url_for('rec_session', id=record_session.id), status=200)

# RECORD ROUTES

@app.route('/record/<int:coll_id>/')
@login_required
def record_session(coll_id):
    app.logger.info(f"{current_user} Requesting record_session")
    collection = Collection.query.get(coll_id)

    if collection.has_assigned_user():
        if current_user.id != collection.assigned_user_id:
            flash("Aðeins skráð rödd getur tekið upp í þessari söfnun", category="danger")
            return redirect(url_for('index'))#+'?msg="Aðeins skráð rödd getur tekið upp í þessari söfnun"&type=danger&icn=times')

    tokens = Token.query.filter(Token.collection_id==coll_id,
        Token.num_recordings==0, Token.marked_as_bad==True).order_by(func.random()).limit(SESSION_SZ)
    return render_template('record.jinja', section='record',
        collection=collection,  tokens=tokens,
        json_tokens=json.dumps([t.get_dict() for t in tokens]),
        tal_api_token=app.config['TAL_API_TOKEN'])
@app.route('/record/token/<int:tok_id>/')
@login_required
def record_single(tok_id):
    app.logger.info(f"{current_user} Requesting record_single")
    token = Token.query.get(tok_id)
    return render_template('record.jinja', tokens=token, section='record',
        single=True, json_tokens=json.dumps([token.get_dict()]),
        tal_api_token=app.config['TAL_API_TOKEN'])

# RATING ROUTES
@app.route('/rate/<int:coll_id>')
@login_required
def rate_session(coll_id):
    app.logger.info(f"{current_user} Requesting rate_session")
    collection = Collection.query.get(coll_id)
    recordings = db.session.query(Recording).order_by(func.random()).limit(SESSION_SZ)
    return render_template('rate.jinja', section='rate',
        json_recordings=json.dumps([r.get_dict() for r in recordings]),
        collection=collection,  recordings=recordings)

# COLLECTION ROUTES

@app.route('/collections/create/', methods=['GET', 'POST'])
@login_required
def create_collection():
    app.logger.info(f"{current_user} Requesting create_collection")
    form = CollectionForm(request.form)
    if request.method == 'POST' and form.validate():
        try:
            # add collection to database
            collection = insert_collection(form)

            return redirect(url_for('collection', id=collection.id))
        except Exception as error:
            app.logger.error(f"Error creating collection {error}\n{traceback.format_exc()}")
    return render_template('collection_create.jinja', form=form,
        section='collection')

@app.route('/collections/')
@login_required
def collection_list():
    app.logger.info(f"{current_user} Requestin collection_list")
    page = int(request.args.get('page', 1))
    sort_by = request.args.get('sort_by', 'name')
    collections = Collection.query.paginate(page,
        per_page=app.config['COLLECTION_PAGINATION'])
    return render_template('collection_list.jinja', collections=collections,
        section='collection')

@app.route('/collections/<int:id>/', methods=['GET', 'POST'])
@login_required
def collection(id):
    app.logger.info(f"{current_user} Requesting collection")
    token_form = BulkTokenForm(request.form)
    if request.method == 'POST':
        tokens = create_tokens(id, request.files.getlist('files'),
            token_form.is_g2p.data)

    page = int(request.args.get('page', 1))
    collection = Collection.query.get(id)
    tokens = ListPagination(collection.tokens, page,
        app.config['TOKEN_PAGINATION'])

    return render_template('collection.jinja',
        collection=collection, token_form=token_form, tokens=tokens,
        section='collection')

@app.route('/collections/<int:id>/sessions', methods=['GET'])
@login_required
def collection_sessions(id):
    app.logger.info(f"{current_user} Requesting collection_sessions")
    page = int(request.args.get('page', 1))
    collection = Collection.query.get(id)
    rec_sessions = ListPagination(collection.sessions, page,
        app.config['SESSION_PAGINATION'])
    return render_template('collection_sessions.jinja',
        collection=collection, sessions=rec_sessions, section='collection')

@app.route('/collections/<int:id>/download/')
@login_required
def download_collection(id):
    app.logger.info(f"{current_user} Requesting download_collection")
    collection = Collection.query.get(id)

    tokens = collection.tokens
    dl_tokens = []
    for token in tokens:
        if token.has_recording:
            dl_tokens.append(token)

    if not os.path.exists('temp'):
        os.makedirs('temp')
    zf = zipfile.ZipFile('temp/{}.zip'.format(collection.name), mode='w')
    index_f = open('./temp/index.tsv', 'w')
    try:
        for token in dl_tokens:
            zf.write(token.get_path(), 'text/{}'.format(token.get_fname()))
            for recording in token.recordings:
                app.logger.info(recording.get_path())
                zf.write(recording.get_path(), 'audio/{}'.format(recording.get_fname()))
                index_f.write('{}\t{}\n'.format(recording.get_fname(), token.get_fname()))
        index_f.close()
        app.logger.info(os.path.abspath(index_f.name))
        zf.write('./temp/index.tsv', 'index.tsv')
    except Exception as error:
        app.logger.error(
            "Error creating a collection .zip : {}\n{}".format(error, traceback.format_exc()))
    finally:
        zf.close()
    @after_this_request
    def remove_file(response):
        try:
            os.remove('temp/{}.zip'.format(collection.name))
            os.remove('temp/index.tsv')
        except Exception as error:
            app.logger.error(
                f"Error deleting a downloaded archive : {error}\n{traceback.format_exc()}")
        return response
    return send_file('temp/{}.zip'.format(collection.name), as_attachment=True)

@app.route('/collections/<int:id>/delete/')
@login_required
@roles_required('admin')
def delete_collection(id):
    collection = db.session.query(Collection).get(id)
    name = collection.name
    shutil.rmtree(collection.get_record_dir())
    shutil.rmtree(collection.get_token_dir())
    db.session.delete(collection)
    db.session.commit()
    flash("{} var eytt".format(name), category='success')
    return redirect(url_for('index'))

# TOKEN ROUTES

@app.route('/tokens/<int:id>/')
@login_required
def token(id):
    app.logger.info(f"{current_user} Requesting token")
    return render_template('token.jinja', token=Token.query.get(id),
        section='token')

@app.route('/tokens/')
@login_required
def token_list():
    app.logger.info(f"{current_user} Requesting token_list")
    page = int(request.args.get('page', 1))
    only_bad = bool(request.args.get('only_bad', False))

    if only_bad:
        tokens = db.session.query(Token).filter_by(marked_as_bad=True).paginate(page,
            per_page=app.config['TOKEN_PAGINATION'])
    else:
        tokens = Token.query.paginate(page,
            per_page=app.config['TOKEN_PAGINATION'])

    return render_template('token_list.jinja', tokens=tokens, only_bad=only_bad, section='token')

@app.route('/tokens/<int:id>/download/')
@login_required
def download_token(id):
    app.logger.info(f"{current_user} Requesting download_token")
    token = Token.query.get(id)
    try:
        return send_from_directory(token.get_directory(), token.fname,
            as_attachment=True)
    except Exception as error:
        app.logger.error(
            f"Error downloading a token : {error}\n{traceback.format_exc()}")

# RECORDING ROUTES

@app.route('/recordings/')
@login_required
def recording_list():
    app.logger.info(f"{current_user} Requesting recording_list")
    page = int(request.args.get('page', 1))
    only_bad = bool(request.args.get('only_bad', False))

    if only_bad:
        recordings = db.session.query(Recording).filter_by(marked_as_bad=True).paginate(page,
            per_page=app.config['RECORDING_PAGINATION'])
    else:
        recordings = Recording.query.order_by(Recording.created_at.desc()).paginate(page,
            per_page=app.config['RECORDING_PAGINATION'])

    return render_template('recording_list.jinja', recordings=recordings, only_bad=only_bad,
        section='recording')

@app.route('/recordings/<int:id>/')
@login_required
def recording(id):
    app.logger.info(f"{current_user} Requesting recording")
    recording = Recording.query.get(id)
    return render_template('recording.jinja', recording=recording, section='recording')

@app.route('/recordings/<int:id>/mark_bad/')
@login_required
def toggle_recording_bad(id):
    app.logger.info(f"{current_user} Requesting toggle_recording_bad")

    recording = Recording.query.get(id)
    recording.marked_as_bad = not recording.marked_as_bad
    db.session.commit()

    return redirect(url_for('recording', id=recording.id))

@app.route('/recordings/<int:id>/mark_bad_ajax/')
@login_required
def toggle_recording_bad_ajax(id):
    app.logger.info(f"{current_user} Requesting toggle_recording_bad")

    recording = Recording.query.get(id)
    state = not recording.marked_as_bad
    recording.marked_as_bad = state
    db.session.commit()

    return Response(str(state), 200)

@app.route('/recordings/<int:id>/download/')
@login_required
def download_recording(id):
    app.logger.info(f"{current_user} Requesting download_recording")

    recording = Recording.query.get(id)
    try:
        return send_from_directory(recording.get_directory(), recording.fname,
            as_attachment=True)
    except Exception as error:
        app.logger.error(
            f"Error downloading a recording : {error}\n{traceback.format_exc()}")

# SESSION ROUTES

@app.route('/sessions/')
@login_required
def rec_session_list():
    app.logger.info(f"{current_user} Requesting rec_session_list")
    page = int(request.args.get('page', 1))
    sessions = Session.query.order_by(Session.created_at.desc()).paginate(page,
        per_page=app.config['SESSION_PAGINATION'])
    return render_template('session_list.jinja', sessions=sessions,
        section='session')

@app.route('/sessions/<int:id>/')
@login_required
def rec_session(id):
    app.logger.info(f"{current_user} Requesting rec_session")
    session = Session.query.get(id)
    return render_template('session.jinja', session=session, section='session')

# USER ROUTES

@app.route('/users/')
@login_required
@roles_required('admin')
def user_list():
    app.logger.info(f"{current_user} Requesting user_list")
    page = int(request.args.get('page', 1))
    users = User.query.paginate(page, app.config['USER_PAGINATION'])
    return render_template('user_list.jinja', users=users, section='user')

@app.route('/users/<int:id>/')
@login_required
def user(id):
    app.logger.info(f"{current_user} Requesting user")
    page = int(request.args.get('page', 1))
    user = User.query.get(id)
    recordings = ListPagination(user.recordings, page,
        app.config['RECORDING_PAGINATION'])
    return render_template("user.jinja", user=user, recordings=recordings,
        section='user')

@app.route('/users/<int:id>/edit/', methods=['GET', 'POST'])
@login_required
def user_edit(id):
    app.logger.info(f"{current_user} Requesting user_edit")

    user = User.query.get(id)
    form = UserEditForm(request.form, obj=user)

    try:
        if request.method == 'POST' and form.validate():
            form.populate_obj(user)
            db.session.commit()
    except Exception as error:
        app.logger.error(f'Error updating a user : {error}\n{traceback.format_exc()}')

    return render_template('model_form.jinja', user=user, form=form, type='edit',
        action=url_for('user_create', id=id), section='user')

@app.route('/users/create/', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def user_create():
    app.logger.info(f"{current_user} Requesting user_create")
    form = ExtendedRegisterForm(request.form)
    if request.method == 'POST' and form.validate():
        try:
            user_datastore.create_user(name=form.name.data, email=form.email.data,
                password=hash_password(form.password.data), roles=[form.role.data])
            db.session.commit()
        except Exception as error:
            app.logger.error(f'Error creating a user : {error}\n{traceback.format_exc()}')
    return render_template('model_form.jinja', form=form, type='create',
        action=url_for('user_create'), section='user')

@app.route('/roles/create/', methods=['GET', 'POST'])
@login_required
#@roles_required('admin')
def role_create():
    app.logger.info(f"{current_user} Requesting role_create")
    form = RoleForm(request.form)
    if request.method == 'POST' and form.validate():
        try:
            role = Role()
            form.populate_obj(role)
            db.session.add(role)
            db.session.commit()
        except Exception as error:
            app.logger.error(f'Error creating a role : {error}\n{traceback.format_exc()}')
    return render_template('model_form.jinja', form=form, type='create',
        action=url_for('role_create'), section='role')

@app.route('/roles/<int:id>/edit/', methods=['GET', 'POST'])
@login_required
def role_edit(id):
    app.logger.info(f"{current_user} Requesting role_edit")

    role = Role.query.get(id)
    form = RoleForm(request.form, obj=role)

    if request.method == 'POST' and form.validate():
        try:
            form.populate_obj(role)
            db.session.commit()
        except Exception as error:
            app.logger.error(f'Error updating a role : {error}\n{traceback.format_exc()}')
    return render_template('model_form.jinja', role=role, form=form, type='edit',
        action=url_for('role_edit', id=id), section='role')

@app.errorhandler(404)
def page_not_found(error):
    flash("Við fundum ekki síðuna sem þú baðst um.", category="warning")
    app.logger.error(f"Page not found {request.path}")
    return redirect(url_for('index'))

@app.errorhandler(500)
def internal_server_error(error):
    flash("Alvarleg villa kom upp, vinsamlega reynið aftur", category="danger")
    app.logger.error('Server Error: %s', (error))
    return redirect(url_for('index'))
