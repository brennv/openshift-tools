# pylint: skip-file

import time

class RouterConfig(OpenShiftCLIConfig):
    ''' RouterConfig is a DTO for the router.  '''
    def __init__(self, rname, namespace, kubeconfig, router_options):
        super(RouterConfig, self).__init__(rname, namespace, kubeconfig, router_options)

class Router(OpenShiftCLI):
    ''' Class to wrap the oc command line tools '''
    def __init__(self,
                 router_config,
                 verbose=False):
        ''' Constructor for OpenshiftOC

           a router consists of 3 or more parts
           - dc/router
           - svc/router
           - endpoint/router
        '''
        super(Router, self).__init__('default', router_config.kubeconfig, verbose)
        self.config = router_config
        self.verbose = verbose
        self.router_parts = [{'kind': 'dc', 'name': self.config.name},
                             {'kind': 'svc', 'name': self.config.name},
                             #{'kind': 'endpoints', 'name': self.config.name},
                            ]

        self.dconfig = None
        self.svc = None
        self.get()

    @property
    def deploymentconfig(self):
        ''' property deploymentconfig'''
        return self.dconfig

    @deploymentconfig.setter
    def deploymentconfig(self, config):
        ''' setter for property deploymentconfig '''
        self.dconfig = config

    @property
    def service(self):
        ''' property service '''
        return self.svc

    @service.setter
    def service(self, config):
        ''' setter for property service '''
        self.svc = config


    def get(self):
        ''' return the self.router_parts '''
        self.service = None
        self.deploymentconfig = None
        for part in self.router_parts:
            result = self._get(part['kind'], rname=part['name'])
            if result['returncode'] == 0 and part['kind'] == 'dc':
                self.deploymentconfig = DeploymentConfig(result['results'][0])
            elif result['returncode'] == 0 and part['kind'] == 'svc':
                self.service = Service(content=result['results'][0])

        return (self.deploymentconfig, self.service)

    def exists(self):
        '''return a whether svc or dc exists '''
        if self.deploymentconfig or self.service:
            return True

        return False

    def delete(self):
        '''return all pods '''
        parts = []
        for part in self.router_parts:
            parts.append(self._delete(part['kind'], part['name']))

        return parts

    def create(self, dryrun=False, output=False, output_type='json'):
        '''Create a deploymentconfig '''
        # We need to create the pem file
        router_pem = '/tmp/router.pem'
        with open(router_pem, 'w') as rfd:
            rfd.write(open(self.config.config_options['cert_file']['value']).read())
            rfd.write(open(self.config.config_options['key_file']['value']).read())
            if self.config.config_options['cacert_file']['value'] and \
               os.path.exists(self.config.config_options['cacert_file']['value']):
                rfd.write(open(self.config.config_options['cacert_file']['value']).read())

        atexit.register(Utils.cleanup, [router_pem])
        self.config.config_options['default_cert']['value'] = router_pem

        options = self.config.to_option_list()

        cmd = ['router', '-n', self.config.namespace]
        cmd.extend(options)
        if dryrun:
            cmd.extend(['--dry-run=True', '-o', 'json'])

        return self.openshift_cmd(cmd, oadm=True, output=output, output_type=output_type)

    def update(self):
        '''run update for the router.  This performs a delete and then create '''
        parts = self.delete()
        for part in parts:
            if part['returncode'] != 0:
                if part.has_key('stderr') and 'not found' in part['stderr']:
                    # the object is not there, continue
                    continue

                # something went wrong
                return parts


        # Ugly built in sleep here.
        time.sleep(15)

        return self.create()

    def needs_update(self, verbose=False):
        ''' check to see if we need to update '''
        if not self.deploymentconfig or not self.service:
            return True

        results = self.create(dryrun=True, output=True, output_type='raw')
        if results['returncode'] != 0:
            return results

        # Since the output from oadm_router is returned as raw
        # we need to parse it.  The first line is the stats_password
        router_results_split = results['results'].split('\n')
        # stats_password = user_dc_results[0]

        # Load the string back into json and get the newly created dc and svc
        json_results = json.loads('\n'.join(router_results_split[1:]))

        # svc
        user_svc = Service(content=json_results['items'][1])

        # Fix the ports to have protocol=TCP
        for port in user_svc.get('spec#ports'):
            port['protocol'] = 'TCP'

        skip = ['portalIP', 'clusterIP', 'sessionAffinity', 'type']
        service_results = Utils.check_def_equal(user_svc.yaml_dict,
                                                self.service.yaml_dict,
                                                skip_keys=skip,
                                                debug=verbose)

        if not service_results:
            return True

        # dc
        user_dc = DeploymentConfig(content=json_results['items'][0])

        # Router needs some exceptions.
        # We do not want to check the autogenerated password for stats admin
        if not self.config.config_options['stats_password']['value']:
            for idx, env_var in enumerate(user_dc.get('spec#template#spec#containers[0]#env') or []):
                if env_var['name'] == 'STATS_PASSWORD':
                    env_var['value'] = \
                      self.deploymentconfig.get('spec#template#spec#containers[0]#env[%s]#value' % idx)

        # dry-run doesn't add the protocol to the ports section.  We will manually do that.
        for idx, port in enumerate(user_dc.get('spec#template#spec#containers[0]#ports') or []):
            if not port.has_key('protocol'):
                port['protocol'] = 'TCP'

        # These are different when generating
        skip = ['dnsPolicy',
                'terminationGracePeriodSeconds',
                'restartPolicy', 'timeoutSeconds',
                'livenessProbe', 'readinessProbe',
                'terminationMessagePath',
                'rollingParams',
               ]

        return not Utils.check_def_equal(user_dc.yaml_dict,
                                         self.deploymentconfig.yaml_dict,
                                         skip_keys=skip,
                                         debug=verbose)
