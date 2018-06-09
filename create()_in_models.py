# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.



@pycompat.implements_to_string
class BaseModel(MetaModel('DummyModel', (object,), {'_register': False})):
    """ Base class for Odoo models.

    Odoo models are created by inheriting:

    *   :class:`Model` for regular database-persisted models

    *   :class:`TransientModel` for temporary data, stored in the database but
        automatically vacuumed every so often

    *   :class:`AbstractModel` for abstract super classes meant to be shared by
        multiple inheriting models

    """
    #
    @api.multi
    def write(self, vals):
        """ write(vals)

        Updates all records in the current set with the provided values.

        :param dict vals: fields to update and the value to set on them e.g::

                {'foo': 1, 'bar': "Qux"}

            will set the field ``foo`` to ``1`` and the field ``bar`` to
            ``"Qux"`` if those are valid (otherwise it will trigger an error).

        :raise AccessError: * if user has no write rights on the requested object
                            * if user tries to bypass access rules for write on the requested object
        :raise ValidateError: if user tries to enter invalid value for a field that is not in selection
        :raise UserError: if a loop would be created in a hierarchy of objects a result of the operation (such as setting an object as its own parent)

        * For numeric fields (:class:`~odoo.fields.Integer`,
          :class:`~odoo.fields.Float`) the value should be of the
          corresponding type
        * For :class:`~odoo.fields.Boolean`, the value should be a
          :class:`python:bool`
        * For :class:`~odoo.fields.Selection`, the value should match the
          selection values (generally :class:`python:str`, sometimes
          :class:`python:int`)
        * For :class:`~odoo.fields.Many2one`, the value should be the
          database identifier of the record to set
        * Other non-relational fields use a string for value

          .. danger::

              for historical and compatibility reasons,
              :class:`~odoo.fields.Date` and
              :class:`~odoo.fields.Datetime` fields use strings as values
              (written and read) rather than :class:`~python:datetime.date` or
              :class:`~python:datetime.datetime`. These date strings are
              UTC-only and formatted according to
              :const:`odoo.tools.misc.DEFAULT_SERVER_DATE_FORMAT` and
              :const:`odoo.tools.misc.DEFAULT_SERVER_DATETIME_FORMAT`
        * .. _openerp/models/relationals/format:

          :class:`~odoo.fields.One2many` and
          :class:`~odoo.fields.Many2many` use a special "commands" format to
          manipulate the set of records stored in/associated with the field.

          This format is a list of triplets executed sequentially, where each
          triplet is a command to execute on the set of records. Not all
          commands apply in all situations. Possible commands are:

          ``(0, _, values)``
              adds a new record created from the provided ``value`` dict.
          ``(1, id, values)``
              updates an existing record of id ``id`` with the values in
              ``values``. Can not be used in :meth:`~.create`.
          ``(2, id, _)``
              removes the record of id ``id`` from the set, then deletes it
              (from the database). Can not be used in :meth:`~.create`.
          ``(3, id, _)``
              removes the record of id ``id`` from the set, but does not
              delete it. Can not be used on
              :class:`~odoo.fields.One2many`. Can not be used in
              :meth:`~.create`.
          ``(4, id, _)``
              adds an existing record of id ``id`` to the set. Can not be
              used on :class:`~odoo.fields.One2many`.
          ``(5, _, _)``
              removes all records from the set, equivalent to using the
              command ``3`` on every record explicitly. Can not be used on
              :class:`~odoo.fields.One2many`. Can not be used in
              :meth:`~.create`.
          ``(6, _, ids)``
              replaces all existing records in the set by the ``ids`` list,
              equivalent to using the command ``5`` followed by a command
              ``4`` for each ``id`` in ``ids``.

          .. note:: Values marked as ``_`` in the list above are ignored and
                    can be anything, generally ``0`` or ``False``.
        """
        if not self:
            return True

        self._check_concurrency()
        self.check_access_rights('write')

        # No user-driven update of these columns
        pop_fields = ['parent_left', 'parent_right']
        if self._log_access:
            pop_fields.extend(MAGIC_COLUMNS)
        for field in pop_fields:
            vals.pop(field, None)

        # split up fields into old-style and pure new-style ones
        old_vals, new_vals, unknown = {}, {}, []
        for key, val in vals.items():
            field = self._fields.get(key)
            if field:
                if field.store or field.inherited:
                    old_vals[key] = val
                if field.inverse and not field.inherited:
                    new_vals[key] = val
            else:
                unknown.append(key)

        if unknown:
            _logger.warning("%s.write() with unknown fields: %s", self._name, ', '.join(sorted(unknown)))

        protected_fields = [self._fields[n] for n in new_vals]
        with self.env.protecting(protected_fields, self):
            # write old-style fields with (low-level) method _write
            if old_vals:
                self._write(old_vals)

            if new_vals:
                self.modified(set(new_vals) - set(old_vals))

                # put the values of fields into cache, and inverse them
                for key in new_vals:
                    field = self._fields[key]
                    # If a field is not stored, its inverse method will probably
                    # write on its dependencies, which will invalidate the field
                    # on all records. We therefore inverse the field one record
                    # at a time.
                    batches = [self] if field.store else list(self)
                    for records in batches:
                        for record in records:
                            record._cache.update(
                                record._convert_to_cache(new_vals, update=True)
                            )
                        field.determine_inverse(records)

                self.modified(set(new_vals) - set(old_vals))

                # check Python constraints for inversed fields
                self._validate_fields(set(new_vals) - set(old_vals))

                # recompute new-style fields
                if self.env.recompute and self._context.get('recompute', True):
                    self.recompute()

        return True

    @api.multi
    def _write(self, vals):
        # low-level implementation of write()
        if not self:
            return True
        self.check_field_access_rights('write', list(vals))

        cr = self._cr

        # for recomputing new-style fields
        extra_fields = ['write_date', 'write_uid'] if self._log_access else []
        self.modified(list(vals) + extra_fields)

        # for updating parent_left, parent_right
        parents_changed = []
        if self._parent_store and (self._parent_name in vals) and \
                not self._context.get('defer_parent_store_computation'):
            # The parent_left/right computation may take up to 5 seconds. No
            # need to recompute the values if the parent is the same.
            #
            # Note: to respect parent_order, nodes must be processed in
            # order, so ``parents_changed`` must be ordered properly.
            parent_val = vals[self._parent_name]
            if parent_val:
                query = "SELECT id FROM %s WHERE id IN %%s AND (%s != %%s OR %s IS NULL) ORDER BY %s" % \
                                (self._table, self._parent_name, self._parent_name, self._parent_order)
                cr.execute(query, (tuple(self.ids), parent_val))
            else:
                query = "SELECT id FROM %s WHERE id IN %%s AND (%s IS NOT NULL) ORDER BY %s" % \
                                (self._table, self._parent_name, self._parent_order)
                cr.execute(query, (tuple(self.ids),))
            parents_changed = [x[0] for x in cr.fetchall()]

        updates = []            # list of (column, expr) or (column, pattern, value)
        upd_todo = []           # list of column names to set explicitly
        updend = []             # list of possibly inherited field names
        direct = []             # list of direcly updated columns
        has_trans = self.env.lang and self.env.lang != 'en_US'
        single_lang = len(self.env['res.lang'].get_installed()) <= 1
        for name, val in vals.items():
            field = self._fields[name]
            if field and field.deprecated:
                _logger.warning('Field %s.%s is deprecated: %s', self._name, name, field.deprecated)
            if field.store:
                if hasattr(field, 'selection') and val:
                    self._check_selection_field_value(name, val)
                if field.column_type:
                    if single_lang or not (has_trans and field.translate is True):
                        # val is not a translation: update the table
                        val = field.convert_to_column(val, self, vals)
                        updates.append((name, field.column_format, val))
                    direct.append(name)
                else:
                    upd_todo.append(name)
            else:
                updend.append(name)

        if self._log_access:
            updates.append(('write_uid', '%s', self._uid))
            updates.append(('write_date', "(now() at time zone 'UTC')"))
            direct.append('write_uid')
            direct.append('write_date')

        if updates:
            self.check_access_rule('write')
            query = 'UPDATE "%s" SET %s WHERE id IN %%s' % (
                self._table, ','.join('"%s"=%s' % (u[0], u[1]) for u in updates),
            )
            params = tuple(u[2] for u in updates if len(u) > 2)
            for sub_ids in cr.split_for_in_conditions(set(self.ids)):
                cr.execute(query, params + (sub_ids,))
                if cr.rowcount != len(sub_ids):
                    raise MissingError(_('One of the records you are trying to modify has already been deleted (Document type: %s).') % self._description)

            # TODO: optimize
            for name in direct:
                field = self._fields[name]
                if callable(field.translate):
                    # The source value of a field has been modified,
                    # synchronize translated terms when possible.
                    self.env['ir.translation']._sync_terms_translations(self._fields[name], self)

                elif has_trans and field.translate:
                    # The translated value of a field has been modified.
                    src_trans = self.read([name])[0][name]
                    if not src_trans:
                        # Insert value to DB
                        src_trans = vals[name]
                        self.with_context(lang=None).write({name: src_trans})
                    val = field.convert_to_column(vals[name], self, vals)
                    tname = "%s,%s" % (self._name, name)
                    self.env['ir.translation']._set_ids(
                        tname, 'model', self.env.lang, self.ids, val, src_trans)

        # invalidate and mark new-style fields to recompute; do this before
        # setting other fields, because it can require the value of computed
        # fields, e.g., a one2many checking constraints on records
        self.modified(direct)

        # defaults in context must be removed when call a one2many or many2many
        rel_context = {key: val
                       for key, val in self._context.items()
                       if not key.startswith('default_')}

        # call the 'write' method of fields which are not columns
        for name in upd_todo:
            field = self._fields[name]
            field.write(self.with_context(rel_context), vals[name])

        # for recomputing new-style fields
        self.modified(upd_todo)

        # write inherited fields on the corresponding parent records
        unknown_fields = set(updend)
        for parent_model, parent_field in self._inherits.items():
            parent_ids = []
            for sub_ids in cr.split_for_in_conditions(self.ids):
                query = "SELECT DISTINCT %s FROM %s WHERE id IN %%s" % (parent_field, self._table)
                cr.execute(query, (sub_ids,))
                parent_ids.extend([row[0] for row in cr.fetchall()])

            parent_vals = {}
            for name in updend:
                field = self._fields[name]
                if field.inherited and field.related[0] == parent_field:
                    parent_vals[name] = vals[name]
                    unknown_fields.discard(name)

            if parent_vals:
                self.env[parent_model].browse(parent_ids).write(parent_vals)

        if unknown_fields:
            _logger.warning('No such field(s) in model %s: %s.', self._name, ', '.join(unknown_fields))

        # check Python constraints
        self._validate_fields(vals)

        # TODO: use _order to set dest at the right position and not first node of parent
        # We can't defer parent_store computation because the stored function
        # fields that are computer may refer (directly or indirectly) to
        # parent_left/right (via a child_of domain)
        if parents_changed:
            if self.pool._init:
                self.pool._init_parent[self._name] = True
            else:
                parent_val = vals[self._parent_name]
                if parent_val:
                    clause, params = '%s=%%s' % self._parent_name, (parent_val,)
                else:
                    clause, params = '%s IS NULL' % self._parent_name, ()

                for id in parents_changed:
                    # determine old parent_left, parent_right of current record
                    cr.execute('SELECT parent_left, parent_right FROM %s WHERE id=%%s' % self._table, (id,))
                    pleft0, pright0 = cr.fetchone()
                    width = pright0 - pleft0 + 1

                    # determine new parent_left of current record; it comes
                    # right after the parent_right of its closest left sibling
                    # (this CANNOT be fetched outside the loop, as it needs to
                    # be refreshed after each update, in case several nodes are
                    # sequentially inserted one next to the other)
                    pleft1 = None
                    cr.execute('SELECT id, parent_right FROM %s WHERE %s ORDER BY %s' % \
                               (self._table, clause, self._parent_order), params)
                    for (sibling_id, sibling_parent_right) in cr.fetchall():
                        if sibling_id == id:
                            break
                        pleft1 = (sibling_parent_right or 0) + 1
                    if not pleft1:
                        # the current record is the first node of the parent
                        if not parent_val:
                            pleft1 = 0          # the first node starts at 0
                        else:
                            cr.execute('SELECT parent_left FROM %s WHERE id=%%s' % self._table, (parent_val,))
                            pleft1 = cr.fetchone()[0] + 1

                    if pleft0 < pleft1 <= pright0:
                        raise UserError(_('Recursivity Detected.'))

                    # make some room for parent_left and parent_right at the new position
                    cr.execute('UPDATE %s SET parent_left=parent_left+%%s WHERE %%s<=parent_left' % self._table, (width, pleft1))
                    cr.execute('UPDATE %s SET parent_right=parent_right+%%s WHERE %%s<=parent_right' % self._table, (width, pleft1))
                    # slide the subtree of the current record to its new position
                    if pleft0 < pleft1:
                        cr.execute('''UPDATE %s SET parent_left=parent_left+%%s, parent_right=parent_right+%%s
                                      WHERE %%s<=parent_left AND parent_left<%%s''' % self._table,
                                   (pleft1 - pleft0, pleft1 - pleft0, pleft0, pright0))
                    else:
                        cr.execute('''UPDATE %s SET parent_left=parent_left-%%s, parent_right=parent_right-%%s
                                      WHERE %%s<=parent_left AND parent_left<%%s''' % self._table,
                                   (pleft0 - pleft1 + width, pleft0 - pleft1 + width, pleft0 + width, pright0 + width))

                self.invalidate_cache(['parent_left', 'parent_right'])

        # recompute new-style fields
        if self.env.recompute and self._context.get('recompute', True):
            self.recompute()

        return True

    #
    # TODO: Should set perm to user.xxx
    #
    @api.model
    @api.returns('self', lambda value: value.id)
    def create(self, vals):
        """ create(vals) -> record

        Creates a new record for the model.

        The new record is initialized using the values from ``vals`` and
        if necessary those from :meth:`~.default_get`.

        :param dict vals:
            values for the model's fields, as a dictionary::

                {'field_name': field_value, ...}

            see :meth:`~.write` for details
        :return: new record created
        :raise AccessError: * if user has no create rights on the requested object
                            * if user tries to bypass access rules for create on the requested object
        :raise ValidateError: if user tries to enter invalid value for a field that is not in selection
        :raise UserError: if a loop would be created in a hierarchy of objects a result of the operation (such as setting an object as its own parent)
        """
        self.check_access_rights('create')

        # add missing defaults, and drop fields that may not be set by user
        vals = self._add_missing_default_values(vals)
        pop_fields = ['parent_left', 'parent_right']
        if self._log_access:
            pop_fields.extend(MAGIC_COLUMNS)
        for field in pop_fields:
            vals.pop(field, None)

        # split up fields into old-style and pure new-style ones
        old_vals, new_vals, unknown = {}, {}, []
        for key, val in vals.items():
            field = self._fields.get(key)
            if field:
                if field.store or field.inherited:
                    old_vals[key] = val
                if field.inverse and not field.inherited:
                    new_vals[key] = val
            else:
                unknown.append(key)

        if unknown:
            _logger.warning("%s.create() includes unknown fields: %s", self._name, ', '.join(sorted(unknown)))

        # create record with old-style fields
        record = self.browse(self._create(old_vals))

        protected_fields = [self._fields[n] for n in new_vals]
        with self.env.protecting(protected_fields, record):
            # put the values of pure new-style fields into cache, and inverse them
            record.modified(set(new_vals) - set(old_vals))
            record._cache.update(record._convert_to_cache(new_vals))
            for key in new_vals:
                self._fields[key].determine_inverse(record)
            record.modified(set(new_vals) - set(old_vals))
            # check Python constraints for inversed fields
            record._validate_fields(set(new_vals) - set(old_vals))
            # recompute new-style fields
            if self.env.recompute and self._context.get('recompute', True):
                self.recompute()

        return record

    @api.model
    def _create(self, vals):
        # data of parent records to create or update, by model
        tocreate = {
            parent_model: {'id': vals.pop(parent_field, None)}
            for parent_model, parent_field in self._inherits.items()
        }

        # list of column assignments defined as tuples like:
        #   (column_name, format_string, column_value)
        #   (column_name, sql_formula)
        # Those tuples will be used by the string formatting for the INSERT
        # statement below.
        updates = [
            ('id', "nextval('%s')" % self._sequence),
        ]

        upd_todo = []
        unknown_fields = []
        protected_fields = []
        for name, val in list(vals.items()):
            field = self._fields.get(name)
            if not field:
                unknown_fields.append(name)
                del vals[name]
            elif field.inherited:
                tocreate[field.related_field.model_name][name] = val
                del vals[name]
            elif not field.store:
                del vals[name]
            elif field.inverse:
                protected_fields.append(field)
        if unknown_fields:
            _logger.warning('No such field(s) in model %s: %s.', self._name, ', '.join(unknown_fields))

        # create or update parent records
        for parent_model, parent_vals in tocreate.items():
            parent_id = parent_vals.pop('id')
            if not parent_id:
                parent_id = self.env[parent_model].create(parent_vals).id
            else:
                self.env[parent_model].browse(parent_id).write(parent_vals)
            vals[self._inherits[parent_model]] = parent_id

        # set boolean fields to False by default (to make search more powerful)
        for name, field in self._fields.items():
            if field.type == 'boolean' and field.store and name not in vals:
                vals[name] = False

        # determine SQL values
        self = self.browse()
        for name, val in vals.items():
            field = self._fields[name]
            if field.store and field.column_type:
                column_val = field.convert_to_column(val, self, vals)
                updates.append((name, field.column_format, column_val))
            else:
                upd_todo.append(name)

            if hasattr(field, 'selection') and val:
                self._check_selection_field_value(name, val)

        if self._log_access:
            updates.append(('create_uid', '%s', self._uid))
            updates.append(('write_uid', '%s', self._uid))
            updates.append(('create_date', "(now() at time zone 'UTC')"))
            updates.append(('write_date', "(now() at time zone 'UTC')"))

        # insert a row for this record
        cr = self._cr
        query = """INSERT INTO "%s" (%s) VALUES(%s) RETURNING id""" % (
                self._table,
                ', '.join('"%s"' % u[0] for u in updates),
                ', '.join(u[1] for u in updates),
            )
        cr.execute(query, tuple(u[2] for u in updates if len(u) > 2))

        # from now on, self is the new record
        id_new, = cr.fetchone()
        self = self.browse(id_new)

        if self._parent_store and not self._context.get('defer_parent_store_computation'):
            if self.pool._init:
                self.pool._init_parent[self._name] = True
            else:
                parent_val = vals.get(self._parent_name)
                if parent_val:
                    # determine parent_left: it comes right after the
                    # parent_right of its closest left sibling
                    pleft = None
                    cr.execute("SELECT parent_right FROM %s WHERE %s=%%s ORDER BY %s" % \
                                    (self._table, self._parent_name, self._parent_order),
                               (parent_val,))
                    for (pright,) in cr.fetchall():
                        if not pright:
                            break
                        pleft = pright + 1
                    if not pleft:
                        # this is the leftmost child of its parent
                        cr.execute("SELECT parent_left FROM %s WHERE id=%%s" % self._table, (parent_val,))
                        pleft = cr.fetchone()[0] + 1
                else:
                    # determine parent_left: it comes after all top-level parent_right
                    cr.execute("SELECT MAX(parent_right) FROM %s" % self._table)
                    pleft = (cr.fetchone()[0] or 0) + 1

                # make some room for the new node, and insert it in the MPTT
                cr.execute("UPDATE %s SET parent_left=parent_left+2 WHERE parent_left>=%%s" % self._table,
                           (pleft,))
                cr.execute("UPDATE %s SET parent_right=parent_right+2 WHERE parent_right>=%%s" % self._table,
                           (pleft,))
                cr.execute("UPDATE %s SET parent_left=%%s, parent_right=%%s WHERE id=%%s" % self._table,
                           (pleft, pleft + 1, id_new))
                self.invalidate_cache(['parent_left', 'parent_right'])

        with self.env.protecting(protected_fields, self):
            # invalidate and mark new-style fields to recompute; do this before
            # setting other fields, because it can require the value of computed
            # fields, e.g., a one2many checking constraints on records
            self.modified(self._fields)

            # defaults in context must be removed when call a one2many or many2many
            rel_context = {key: val
                           for key, val in self._context.items()
                           if not key.startswith('default_')}

            # call the 'write' method of fields which are not columns
            for name in sorted(upd_todo, key=lambda name: self._fields[name]._sequence):
                field = self._fields[name]
                field.write(self.with_context(rel_context), vals[name], create=True)

            # for recomputing new-style fields
            self.modified(upd_todo)

            # check Python constraints
            self._validate_fields(vals)

            if self.env.recompute and self._context.get('recompute', True):
                # recompute new-style fields
                self.recompute()

        self.check_access_rule('create')

        if self.env.lang and self.env.lang != 'en_US':
            # add translations for self.env.lang
            for name, val in vals.items():
                field = self._fields[name]
                if field.store and field.column_type and field.translate is True:
                    tname = "%s,%s" % (self._name, name)
                    self.env['ir.translation']._set_ids(tname, 'model', self.env.lang, self.ids, val, val)

        return id_new



AbstractModel = BaseModel

class Model(AbstractModel):
    """ Main super-class for regular database-persisted Odoo models.

    Odoo models are created by inheriting from this class::

        class user(Model):
            ...

    The system will later instantiate the class once per database (on
    which the class' module is installed).
    """
    _auto = True                # automatically create database backend
    _register = False           # not visible in ORM registry, meant to be python-inherited only
    _abstract = False           # not abstract
    _transient = False          # not transient


