# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import sys

from collections import defaultdict

from .util import CLIError, CommandResultItem, todict
from .parser import CLICommandParser
from .commands import CLICommandsLoader
from .events import (EVENT_INVOKER_PRE_CMD_TBL_CREATE, EVENT_INVOKER_POST_CMD_TBL_CREATE,
                     EVENT_INVOKER_CMD_TBL_LOADED, EVENT_INVOKER_PRE_PARSE_ARGS,
                     EVENT_INVOKER_POST_PARSE_ARGS, EVENT_INVOKER_TRANSFORM_RESULT,
                     EVENT_INVOKER_FILTER_RESULT)
from .help import CLIHelp


class CommandInvoker(object):

    def __init__(self,
                 ctx=None,
                 parser_cls=CLICommandParser,
                 commands_loader_cls=CLICommandsLoader,
                 help_cls=CLIHelp,
                 initial_data=None):
        self.ctx = ctx
        # In memory collection of key-value data for this current invocation This does not persist between invocations.
        self.data = initial_data or defaultdict(lambda: None)
        self.data['command'] = 'unknown'
        self._global_parser = parser_cls.create_global_parser(ctx=self.ctx)
        self.parser = parser_cls(ctx=self.ctx, prog=self.ctx.name, parents=[self._global_parser])
        self.commands_loader = commands_loader_cls(ctx=self.ctx)
        self.help = help_cls(ctx=self.ctx)

    def _filter_params(self, args):  # pylint: disable=no-self-use
        # Consider - we are using any args that start with an underscore (_) as 'private'
        # arguments and remove them from the arguments that we pass to the actual function.
        params = dict([(key, value)
                       for key, value in args.__dict__.items()
                       if not key.startswith('_')])
        params.pop('func', None)
        params.pop('command', None)
        return params

    def _rudimentary_get_command(self, args):  # pylint: disable=no-self-use
        """ Rudimentary parsing to get the command """
        nouns = []
        for i, current in enumerate(args):
            try:
                if current[0] == '-':
                    break
            except IndexError:
                pass
            args[i] = current.lower()
            nouns.append(args[i])
        return ' '.join(nouns)

    def _validate_cmd_level(self, ns, cmd_validator):  # pylint: disable=no-self-use
        if cmd_validator:
            cmd_validator(ns)
        try:
            delattr(ns, '_command_validator')
        except AttributeError:
            pass

    def _validate_arg_level(self, ns, **_):  # pylint: disable=no-self-use
        for validator in getattr(ns, '_argument_validators', []):
            validator(ns)
        try:
            delattr(ns, '_argument_validators')
        except AttributeError:
            pass

    def _validation(self, parsed_ns):
        try:
            cmd_validator = getattr(parsed_ns, '_command_validator', None)
            if cmd_validator:
                self._validate_cmd_level(parsed_ns, cmd_validator)
            else:
                self._validate_arg_level(parsed_ns)
        except CLIError:
            raise
        except Exception:  # pylint: disable=broad-except
            err = sys.exc_info()[1]
            getattr(parsed_ns, '_parser', self.parser).validation_error(str(err))

    def execute(self, args):
        self.ctx.raise_event(EVENT_INVOKER_PRE_CMD_TBL_CREATE, args=args)
        cmd_tbl = self.commands_loader.load_command_table(args)
        command = self._rudimentary_get_command(args)
        self.commands_loader.load_arguments(command)
        self.ctx.raise_event(EVENT_INVOKER_POST_CMD_TBL_CREATE, cmd_tbl=cmd_tbl)
        self.parser.load_command_table(cmd_tbl)
        self.ctx.raise_event(EVENT_INVOKER_CMD_TBL_LOADED, parser=self.parser)
        if not args:
            self.ctx.completion.enable_autocomplete(self.parser)
            subparser = self.parser.subparsers[tuple()]
            self.help.show_welcome(subparser)
            return None

        if args[0].lower() == 'help':
            args[0] = '--help'

        self.ctx.completion.enable_autocomplete(self.parser)

        self.ctx.raise_event(EVENT_INVOKER_PRE_PARSE_ARGS, args=args)
        parsed_args = self.parser.parse_args(args)
        self.ctx.raise_event(EVENT_INVOKER_POST_PARSE_ARGS, command=parsed_args.command, args=parsed_args)

        self._validation(parsed_args)

        self.data['command'] = parsed_args.command

        params = self._filter_params(parsed_args)

        cmd_result = parsed_args.func(params)
        cmd_result = todict(cmd_result)

        event_data = {'result': cmd_result}
        self.ctx.raise_event(EVENT_INVOKER_TRANSFORM_RESULT, event_data=event_data)
        self.ctx.raise_event(EVENT_INVOKER_FILTER_RESULT, event_data=event_data)

        return CommandResultItem(event_data['result'],
                                 table_transformer=cmd_tbl[parsed_args.command].table_transformer,
                                 is_query_active=self.data['query_active'])
