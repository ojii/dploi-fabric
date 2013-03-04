import StringIO
from django.utils.text import slugify
from fabric.decorators import task
from fabric.api import run, env, put
from dploi_fabric.toolbox.nginx_dsl import Nginx, prettify

from dploi_fabric.utils import config


@task(alias="reload")
def reload_nginx():
    run('sudo /etc/init.d/nginx reload')

def _default_proxy_pass_config(section, upstream_name, ssl):
    section.config('proxy_pass', 'http://%s' % upstream_name)
    section.config('proxy_redirect', 'off')
    section.config('proxy_set_header', 'Host', '$host')
    section.config('proxy_set_header', 'X-Real-IP', '$remote_addr')
    section.config('proxy_set_header', 'X-Forwarded-For', '$proxy_add_x_forwarded_for')
    if ssl:
        section.config('proxy_set_header', 'X-Forwarded-Protocol', 'https')
        section.config('proxy_set_header', 'X-Forwarded-SSL', 'on')
    else:
        section.config('proxy_set_header', 'X-Forwarded-Protocol', 'http')
        section.config('proxy_set_header', 'X-Forwarded-SSL', 'off')
    section.config('client_body_buffer_size', '128k')
    section.config('proxy_connect_timeout', 90)
    section.config('proxy_send_timeout', 90)
    section.config('proxy_read_timeout', 90)
    section.config('proxy_buffer_size', '4k')
    section.config('proxy_buffers', 4, '32k')
    section.config('proxy_busy_buffers_size', '64k')
    section.config('proxy_temp_file_writer_size', '64k')

def render_config():
    full = []
    for site, site_config in config.sites.items():
        conf = Nginx()
        domains = site_config.deployment.get("domains")[site]
        upstream_name = slugify(u" ".join(domains))
        with conf.section('upstream', upstream_name) as upstream:
            for process in [site_config.processes[x] for x in site_config.processes if site_config.processes[x]["type"] == "gunicorn"]:
                upstream.config('server', 'unix:%s' % process['socket'], 'fail_timeout=0')

        deployment = site_config['deployment']

        listen = '%s:%s' % (deployment['bind_ip'], '443' if deployment['ssl'] else '80')

        with conf.server(listen, *domains) as server:
            if deployment['ssl']:
                server.config('ssl', 'on')
                server.config('ssl_certificate', deployment['ssl_cert_path'])
                server.config('ssl_certificate_key', deployment['ssl_key_path'])

            server.config('access_log', '%s../log/nginx/access.log' % deployment['path'])
            server.config('error_log', '%s../log/nginx/error.log' % deployment['path'])

            with server.section('location', '/') as root:
                _default_proxy_pass_config(root, upstream_name, deployment['ssl'])
                for key, value in site_config['nginx'].items():
                    root.config(key, value)

            for location, max_body_size in deployment['big_body_endpoints']:
                with server.section('location', location) as big_body_endpoint:
                    _default_proxy_pass_config(big_body_endpoint, upstream_name, deployment['ssl'])
                    big_body_endpoint.config('client_max_body_size', max_body_size)

            for url, relpath in site_config['static'].items():
                with server.section('location', url) as static:
                    static.config('access_log', 'off')
                    static.config('alias', '%s%s' % (deployment['path'], relpath))

            for url, relpath in site_config['sendfile'].items():
                with server.section('location', url) as sendfile:
                    sendfile.config('internal')
                    sendfile.config('alias', '%s%s' % (deployment['path'], relpath))

            for redirect in deployment['url_redirect']:
                server.config('rewrite', redirect['source'], redirect['destination'], redirect.get('options', 'permanent'))

            if deployment['basic_auth']:
                server.config('auth_basic', '"Restricted"')
                server.config('auth_basic_user_file', site_config['basic_auth_path'])

            for codes, filename, root in deployment['static_error_pages']:
                location_name = '/%s' % filename
                args = tuple(codes) + (location_name,)
                server.config('error_page', *args)

                with server.section('location', '=', location_name) as static_error_page:
                    static_error_page.config('root', root)
                    static_error_page.config('allow', 'all')

        if deployment['ssl']:
            with conf.server('%s:80' % deployment['bind_ip'], *domains) as http_server:
                http_server.config('rewrite', '^(.*)', 'https://$host$1', 'permanent')

        for redirect in deployment['domains_redirect']:
            with conf.server('%s:80' % deployment['bind_ip'], redirect['domain']) as domain_redirect:
                domain_redirect.config('rewrite', '^(.*)', 'http://%s$1' % redirect['destination_domain'], 'permanent')
                domain_redirect.config('access_log', '%s../log/nginx/access.log' % deployment['path'])
                domain_redirect.config('error_log', '%s../log/nginx/error.log' % deployment['path'])

            if deployment['ssl']:
                with conf.server('%s:443' % deployment['bind_ip'], redirect['domain']) as secure_domain_redirect:
                    secure_domain_redirect.config('ssl', 'on')
                    secure_domain_redirect.config('ssl_certificate', deployment['ssl_cert_path'])
                    secure_domain_redirect.config('ssl_certificate_key', deployment['ssl_key_path'])
                    secure_domain_redirect.config('rewrite', '^(.*)', 'http://%s$1' % redirect['destination_domain'], 'permanent')
                    secure_domain_redirect.config('access_log', '%s../log/nginx/access.log' % deployment['path'])
                    secure_domain_redirect.config('error_log', '%s../log/nginx/error.log' % deployment['path'])
        deployment['postprocess_nginx_conf'](conf)
        full.extend(prettify(conf))
    return '\n'.join(full)

@task
def update_config_file():
    output = render_config()
    put(StringIO.StringIO(output), '%(path)s/../config/nginx.conf' % env)
    reload_nginx()
