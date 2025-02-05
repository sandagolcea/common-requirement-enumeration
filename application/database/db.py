import logging
import re
from collections import Counter
from itertools import permutations
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx  # type: ignore
import yaml
from flask_sqlalchemy.model import DefaultMeta
from sqlalchemy import func

from application.defs import cre_defs
from application.utils import file  # type: ignore

from .. import sqla  # type: ignore

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


BaseModel: DefaultMeta = sqla.Model


class Standard(BaseModel):  # type: ignore

    __tablename__ = "standard"
    id = sqla.Column(sqla.Integer, primary_key=True)
    # ASVS or standard name,  what are we linking to
    name = sqla.Column(sqla.String)
    # which part of <name> are we linking to
    section = sqla.Column(sqla.String, nullable=False)
    # which subpart of <name> are we linking to
    subsection = sqla.Column(sqla.String)
    tags = sqla.Column(sqla.String, default="")  # coma separated tags
    version = sqla.Column(sqla.String, default="")

    # some external link to where this is, usually a URL with an anchor
    link = sqla.Column(sqla.String, default="")
    __table_args__ = (
        sqla.UniqueConstraint(name, section, subsection, name="standard_section"),
    )


class CRE(BaseModel):  # type: ignore

    __tablename__ = "cre"
    id = sqla.Column(sqla.Integer, primary_key=True)

    external_id = sqla.Column(sqla.String, default="")
    description = sqla.Column(sqla.String, default="")
    name = sqla.Column(sqla.String)
    tags = sqla.Column(sqla.String, default="")  # coma separated tags

    __table_args__ = (
        sqla.UniqueConstraint(name, external_id, name="unique_cre_fields"),
    )


class InternalLinks(BaseModel):  # type: ignore
    # model cre-groups linking cres
    __tablename__ = "crelinks"
    type = sqla.Column(sqla.String, default="SAME")

    group = sqla.Column(sqla.Integer, sqla.ForeignKey("cre.id"), primary_key=True)
    cre = sqla.Column(sqla.Integer, sqla.ForeignKey("cre.id"), primary_key=True)


class Links(BaseModel):  # type: ignore

    __tablename__ = "links"
    type = sqla.Column(sqla.String, default="SAM")
    cre = sqla.Column(sqla.Integer, sqla.ForeignKey("cre.id"), primary_key=True)
    standard = sqla.Column(
        sqla.Integer, sqla.ForeignKey("standard.id"), primary_key=True
    )


class Standard_collection:
    def __init__(self) -> None:
        self.session = sqla.session
        self.cre_graph = self.__load_cre_graph()

    def __load_cre_graph(self) -> nx.Graph:

        graph = nx.DiGraph()
        for il in self.session.query(InternalLinks).all():
            graph.add_node(f"CRE: {il.group}")
            graph.add_node(f"CRE: {il.cre}")
            graph.add_edge(f"CRE: {il.group}", f"CRE: {il.cre}")

        for lnk in self.session.query(Links).all():
            graph.add_node(f"Standard: {str(lnk.standard)}")
            graph.add_edge(f"CRE: {lnk.cre}", f"Standard: {str(lnk.standard)}")
        return graph

    def __get_external_links(self) -> List[Tuple[CRE, Standard, str]]:
        external_links: List[Tuple[CRE, Standard, str]] = []

        all_links = self.session.query(Links).all()
        for link in all_links:
            cre = self.session.query(CRE).filter(CRE.id == link.cre).first()
            standard = (
                self.session.query(Standard)
                .filter(Standard.id == link.standard)
                .first()
            )
            external_links.append((cre, standard, link.type))
        return external_links

    def __get_internal_links(self) -> List[Tuple[CRE, CRE, str]]:

        internal_links = []
        all_internal_links = self.session.query(InternalLinks).all()
        for il in all_internal_links:
            group = self.session.query(CRE).filter(CRE.id == il.group).first()
            cre = self.session.query(CRE).filter(CRE.id == il.cre).first()
            internal_links.append((group, cre, il.type))
        return internal_links

    def __get_unlinked_standards(self) -> List[Standard]:

        linked_standards = (
            self.session.query(Standard.id)
            .join(Links)
            .filter(Standard.id == Links.standard)
        )

        standards: List[Standard] = (
            self.session.query(Standard)
            .filter(Standard.id.notin_(linked_standards))
            .all()
        )

        return standards

    def get_standards_names(self) -> List[str]:

        # this returns a tuple of (str,nothing)
        q = self.session.query(Standard.name).distinct().all()
        res = [i[0] for i in q]
        return res

    def get_max_internal_connections(self) -> int:
        q = self.session.query(InternalLinks).all()
        grp_count = Counter([x.group for x in q]) or {0: 0}
        cre_count = Counter([x.cre for x in q]) or {0: 0}
        return max([max(cre_count.values()), max(grp_count.values())])

    def find_cres_of_cre(self, cre: CRE) -> Optional[List[CRE]]:
        """returns the higher level CREs of the cre or none
        if no higher level cres link to it"""
        cre_id = self.session.query(CRE.id).filter(CRE.name == cre.name).first()
        links = (
            self.session.query(InternalLinks).filter(InternalLinks.cre == cre_id).all()
        )
        if links:
            result = []
            for link in links:
                result.append(
                    self.session.query(CRE).filter(CRE.id == link.group).first()
                )
            return result

        return None

    def find_cres_of_standard(self, standard: Standard) -> Optional[List[CRE]]:
        """returns the CREs that link to this standard or none
        if none link to it"""
        if not standard.id:
            standard = (
                self.session.query(Standard)
                .filter(
                    sqla.and_(
                        Standard.name == standard.name,
                        Standard.section == standard.section,
                        Standard.subsection == standard.subsection,
                        Standard.version == standard.version,
                    )
                )
                .first()
            )
        if not standard:

            return None
        result: List[CRE] = []
        for link in (
            self.session.query(Links).filter(Links.standard == standard.id).all()
        ):
            result.append(self.session.query(CRE).filter(CRE.id == link.cre).first())
        return result or None

    def get_by_tags(self, tags: List[str]) -> List[cre_defs.Document]:
        """Returns the cre_defs.Documents and their Links
        that are tagged with ALL of the tags provided
        """
        # TODO: (spyros), when we have useful tags this needs to be refactored
        #  so both standards and CREs become the same query
        #  and it gets paginated
        standards_where_clause = []
        cre_where_clause = []
        documents = []

        if not tags:
            return []

        for tag in tags:
            standards_where_clause.append(
                sqla.and_(Standard.tags.like("%{}%".format(tag)))
            )
            cre_where_clause.append(sqla.and_(CRE.tags.like("%{}%".format(tag))))

        standards = Standard.query.filter(*standards_where_clause).all() or []
        for standard in standards:
            standard = self.get_standards(
                name=standard.name,
                section=standard.section,
                subsection=standard.subsection,
                version=standard.version,
                link=standard.link,
            )
            if standard:
                documents.extend(standard)
            else:
                logger.fatal(
                    "db.get_standard returned None for"
                    "Standard %s:%s that exists, BUG!"
                    % (standard.name, standard.section)
                )

        cres = CRE.query.filter(*cre_where_clause).all() or []
        for c in cres:
            cre = self.get_CREs(external_id=c.external_id, name=c.name)[0]
            if cre:
                documents.append(cre)
            else:
                logger.fatal(
                    "db.get_CRE returned None for CRE %s:%s that exists, BUG!"
                    % (c.id, c.name)
                )
        return documents

    def get_standards_with_pagination(
        self,
        name: str,
        section: Optional[str] = None,
        subsection: Optional[str] = None,
        link: Optional[str] = None,
        version: Optional[str] = None,
        partial: Optional[bool] = False,
        page: int = 0,
        items_per_page: Optional[int] = None,
        include_only: Optional[List[str]] = None,
    ) -> Tuple[
        Optional[int], Optional[List[cre_defs.Standard]], Optional[List[Standard]]
    ]:
        """
        Returns the relevant standard entries and their linked CREs
        include_only: If set, only the CRE ids in the list provided will be returned
        If a standard entry is not linked to by a CRE in the list the Standard entry will be returned empty.
        """
        standards = []
        dbstands = self.__get_standards_query__(
            name, section, subsection, link, version, partial
        ).paginate(int(page), items_per_page, False)
        total_pages = dbstands.pages
        if dbstands.items:
            for dbstand in dbstands.items:
                standard = StandardFromDB(dbstandard=dbstand)
                linked_cres = Links.query.filter(Links.standard == dbstand.id).all()
                for dbcre_link in linked_cres:
                    dbcre = CRE.query.filter(CRE.id == dbcre_link.cre).first()
                    if dbcre:
                        if not include_only or (
                            include_only
                            and (
                                dbcre.external_id in include_only
                                or dbcre.name in include_only
                            )
                        ):
                            standard.add_link(
                                cre_defs.Link(
                                    ltype=cre_defs.LinkTypes.from_str(dbcre_link.type),
                                    document=CREfromDB(dbcre),
                                )
                            )
                standards.append(standard)
            return total_pages, standards, dbstands
        else:
            logger.warning("Standard %s does not exist in the db" % (name))
            return None, None, None

    # TODO(spyros): merge with above and make "paginate" a boolean switch
    def get_standards(
        self,
        name: Optional[str] = None,
        section: Optional[str] = None,
        subsection: Optional[str] = None,
        link: Optional[str] = None,
        version: Optional[str] = None,
        partial: Optional[bool] = False,
        include_only: Optional[List[str]] = None,
    ) -> Optional[List[cre_defs.Standard]]:

        standards = []
        standards_query = self.__get_standards_query__(
            name=name,
            section=section,
            subsection=subsection,
            link=link,
            version=version,
            partial=partial,
        )
        dbstands = standards_query.all()
        if dbstands:
            for dbstand in dbstands:
                standard = StandardFromDB(dbstandard=dbstand)
                linked_cres = Links.query.filter(Links.standard == dbstand.id).all()
                for dbcre_link in linked_cres:
                    dbcre = CRE.query.filter(CRE.id == dbcre_link.cre).first()
                    if not include_only or (
                        include_only
                        and (
                            dbcre.external_id in include_only
                            or dbcre.name in include_only
                        )
                    ):
                        standard.add_link(
                            cre_defs.Link(
                                ltype=cre_defs.LinkTypes.from_str(dbcre_link.type),
                                document=CREfromDB(dbcre),
                            )
                        )
                standards.append(standard)
            return standards
        else:
            logger.warning("Standard %s does not exist in the db" % (name))

            return None

    def __get_standards_query__(
        self,
        name: Optional[str] = None,
        section: Optional[str] = None,
        subsection: Optional[str] = None,
        link: Optional[str] = None,
        version: Optional[str] = None,
        partial: Optional[bool] = False,
    ) -> sqla.Query:
        if not name and not section and not subsection and not link and not version:
            raise ValueError("tried to retrieve standard with no values")
        query = Standard.query
        if name:
            if not partial:
                query = Standard.query.filter(func.lower(Standard.name) == name.lower())
            else:
                query = Standard.query.filter(
                    func.lower(Standard.name).like(name.lower())
                )
        if section:
            if not partial:
                query = query.filter(func.lower(Standard.section) == section.lower())
            else:
                query = query.filter(func.lower(Standard.section).like(section.lower()))
        if subsection:
            if not partial:
                query = query.filter(
                    func.lower(Standard.subsection) == subsection.lower()
                )
            else:
                query = query.filter(
                    func.lower(Standard.subsection).like(subsection.lower())
                )
        if link:
            if not partial:
                query = query.filter(Standard.link == link)
            else:
                query = query.filter(Standard.link.like(link))
        if version:
            if not partial:
                query = query.filter(Standard.version == version)
            else:
                query = query.filter(Standard.version.like(version))
        return query

    def get_CREs(
        self,
        external_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        partial: Optional[bool] = False,
        include_only: Optional[List[str]] = None,
    ) -> Optional[List[cre_defs.CRE]]:
        cres: Optional[List[cre_defs.CRE]] = []
        query = CRE.query
        if not external_id and not name and not description:
            logger.error("You need to search by external_id name or description")
            return None

        if external_id:
            if not partial:
                query = query.filter(CRE.external_id == external_id)
            else:
                query = query.filter(CRE.external_id.like(external_id))
        if name:
            if not partial:
                query = query.filter(func.lower(CRE.name) == name.lower())
            else:
                query = query.filter(func.lower(CRE.name).like(name.lower()))
        if description:
            if not partial:
                query = query.filter(func.lower(CRE.description) == description.lower())
            else:
                query = query.filter(
                    func.lower(CRE.description).like(description.lower())
                )
        dbcres = query.all()
        if not dbcres:
            logger.warning(
                "CRE %s:%s:%s does not exist in the db"
                % (external_id, name, description)
            )
            return None

        # todo figure a way to return both the Standard
        # and the link_type for that link
        for dbcre in dbcres:
            cre = CREfromDB(dbcre)
            linked_standards = (
                self.session.query(Links).filter(Links.cre == dbcre.id).all()
            )
            for ls in linked_standards:
                stnd = (
                    self.session.query(Standard)
                    .filter(Standard.id == ls.standard)
                    .first()
                )
                if not include_only or (include_only and stnd.name in include_only):
                    cre.add_link(
                        cre_defs.Link(
                            document=StandardFromDB(stnd),
                            ltype=cre_defs.LinkTypes.from_str(ls.type),
                        )
                    )
            # todo figure the query to merge the following two
            internal_links = (
                self.session.query(InternalLinks)
                .filter(
                    sqla.or_(
                        InternalLinks.cre == dbcre.id, InternalLinks.group == dbcre.id
                    )
                )
                .all()
            )
            for il in internal_links:
                q = self.session.query(CRE)

                res: CRE
                ltype = cre_defs.LinkTypes.from_str(il.type)

                if il.cre == dbcre.id:
                    res = q.filter(CRE.id == il.group).first()
                    # if this CRE is the lower level cre the relationship will be tagged "Contains"
                    # in that case the implicit relationship is "Is Part Of"
                    # otherwise the relationship will be "Related" and we don't need to do anything
                    if ltype == cre_defs.LinkTypes.Contains:
                        # important, this is the only implicit link we have for now
                        ltype = cre_defs.LinkTypes.PartOf
                elif il.group == dbcre.id:
                    res = q.filter(CRE.id == il.cre).first()
                    ltype = cre_defs.LinkTypes.from_str(il.type)
                cre.add_link(cre_defs.Link(document=CREfromDB(res), ltype=ltype))
            cres.append(cre)
        return cres

    def export(self, dir: str) -> List[cre_defs.Document]:
        """Exports the database to a CRE file collection on disk"""
        docs: Dict[str, cre_defs.Document] = {}
        cre, standard = None, None

        # internal links are Group/HigherLevelCRE -> CRE
        for link in self.__get_internal_links():
            group = link[0]
            cre = link[1]
            type = link[2]
            grp = None
            # when cres link to each other it's a two way link
            # so handle cre1(group) -> cre2 link first
            if group.name in docs.keys():
                grp = docs[group.name]
            else:
                grp = CREfromDB(group)
            grp.add_link(
                cre_defs.Link(
                    ltype=cre_defs.LinkTypes.from_str(type), document=CREfromDB(cre)
                )
            )
            docs[group.name] = grp

            # then handle cre2 -> cre1 link
            if cre.name in docs.keys():
                c = docs[cre.name]
            else:
                c = CREfromDB(cre)
            docs[cre.name] = c
            # this cannot be grp, grp already has a link to cre2
            c.add_link(
                cre_defs.Link(
                    ltype=cre_defs.LinkTypes.from_str(type), document=CREfromDB(group)
                )
            )

        # external links are CRE -> standard
        for link in self.__get_external_links():
            internal_doc = link[0]
            standard = link[1]
            type = link[2]
            cr = None
            grp = None
            if internal_doc.name in docs.keys():
                cr = docs[internal_doc.name]
            else:
                cr = CREfromDB(internal_doc)
            if len(standard.name) != 0:
                cr.add_link(
                    cre_defs.Link(
                        ltype=cre_defs.LinkTypes.from_str(type),
                        document=StandardFromDB(standard),
                    )
                )
            docs[cr.name] = cr

        # unlinked standards last
        for ustandard in self.__get_unlinked_standards():
            ustand = StandardFromDB(ustandard)
            docs[
                "%s-%s:%s:%s"
                % (ustand.name, ustand.section, ustand.subsection, ustand.version)
            ] = ustand

        for _, doc in docs.items():
            title = doc.name.replace("/", "-") + ".yaml"
            file.writeToDisk(
                file_title=title,
                file_content=yaml.safe_dump(doc.todict()),
                cres_loc=dir,
            )

        return list(docs.values())

    def add_cre(self, cre: cre_defs.CRE) -> CRE:
        entry: CRE
        query: sqla.Query = self.session.query(CRE).filter(
            func.lower(CRE.name) == cre.name.lower()
        )
        if cre.id:
            entry = query.filter(CRE.external_id == cre.id).first()
        else:
            entry = query.filter(
                func.lower(CRE.description) == cre.description.lower()
            ).first()

        if entry is not None:
            logger.debug("knew of %s ,updating" % cre.name)
            if not entry.external_id:
                if entry.external_id != cre.id:
                    raise ValueError(
                        f"Attempting to register existing CRE"
                        f"{entry.external_id}:{entry.name} with other ID {cre.id}"
                    )
                entry.external_id = cre.id
            if not entry.description:
                entry.description = cre.description
            if not entry.tags:
                entry.tags = ",".join(cre.tags)
            return entry
        else:
            logger.debug("did not know of %s ,adding" % cre.name)
            entry = CRE(
                description=cre.description,
                name=cre.name,
                external_id=cre.id,
                tags=",".join([str(t) for t in cre.tags]),
            )
            self.session.add(entry)
            self.session.commit()
            self.cre_graph.add_node(f"CRE: {entry.id}")
        return entry

    def add_standard(self, standard: cre_defs.Standard) -> Standard:
        entry: Standard = Standard.query.filter(
            sqla.and_(
                func.lower(Standard.name) == standard.name.lower(),
                func.lower(Standard.section) == standard.section.lower(),
                func.lower(Standard.subsection) == standard.subsection.lower(),
                func.lower(Standard.version) == standard.version.lower(),
            )
        ).first()
        if entry is not None:
            logger.debug(f"knew of {entry.name}:{entry.section} ,updating")
            entry.link = standard.hyperlink
            self.session.commit()
            return entry
        else:
            logger.debug(f"did not know of {standard.name}:{standard.section} ,adding")
            entry = Standard(
                name=standard.name,
                section=standard.section,
                subsection=standard.subsection,
                link=standard.hyperlink,
                version=standard.version,
                tags=",".join([str(t) for t in standard.tags]),
            )
            self.session.add(entry)
            self.session.commit()
            self.cre_graph.add_node("Standard: " + str(entry.id))
        return entry

    def __introduces_cycle(self, node_from: str, node_to: str) -> Any:

        try:
            existing_cycle = nx.find_cycle(self.cre_graph)
            if existing_cycle:
                logger.fatal(
                    "Existing graph contains cycle,"
                    "this not a recoverable error,"
                    f" manual database actions are required {existing_cycle}"
                )
                raise ValueError(
                    "Existing graph contains cycle,"
                    "this not a recoverable error,"
                    f" manual database actions are required {existing_cycle}"
                )
        except nx.exception.NetworkXNoCycle:
            pass  # happy path, we don't want cycles
        new_graph = self.cre_graph.copy()
        new_graph.add_edge(node_from, node_to)
        try:
            return nx.find_cycle(new_graph)
        except nx.NetworkXNoCycle:
            return False

    def add_internal_link(
        self, group: CRE, cre: CRE, type: cre_defs.LinkTypes = cre_defs.LinkTypes.Same
    ) -> None:

        if cre.id is None:
            if cre.external_id is None:
                cre = (
                    self.session.query(CRE)
                    .filter(
                        sqla.and_(
                            CRE.name == cre.name, CRE.description == cre.description
                        )
                    )
                    .first()
                )
            else:
                cre = (
                    self.session.query(CRE)
                    .filter(
                        sqla.and_(
                            CRE.name == cre.name, CRE.external_id == cre.external_id
                        )
                    )
                    .first()
                )
        if group.id is None:
            if group.external_id is None:
                group = (
                    self.session.query(CRE)
                    .filter(
                        sqla.and_(
                            CRE.name == group.name, CRE.description == group.description
                        )
                    )
                    .first()
                )
            else:
                group = (
                    self.session.query(CRE)
                    .filter(
                        sqla.and_(
                            CRE.name == group.name, CRE.external_id == group.external_id
                        )
                    )
                    .first()
                )
        if cre is None or group is None:
            logger.fatal(
                "Tried to insert internal mapping with element"
                " that doesn't exist in db, this looks like a bug"
            )
            return None

        entry = (
            self.session.query(InternalLinks)
            .filter(
                sqla.or_(
                    sqla.and_(
                        InternalLinks.cre == group.id, InternalLinks.group == cre.id
                    ),
                    sqla.and_(
                        InternalLinks.cre == cre.id, InternalLinks.group == group.id
                    ),
                )
            )
            .first()
        )
        if entry is not None:
            logger.debug(
                f"knew of internal link {cre.name} == {group.name} of type {entry.type},updating to type {type.value}"
            )
            entry.type = type.value
            self.session.commit()

            return None

        else:
            logger.debug(
                "did not know of internal link"
                f" {group.external_id}:{group.name}"
                f" == {cre.external_id}:{cre.name} ,adding"
            )
            cycle = self.__introduces_cycle(f"CRE: {group.id}", f"CRE: {cre.id}")
            if not cycle:
                self.session.add(
                    InternalLinks(type=type.value, cre=cre.id, group=group.id)
                )
                self.session.commit()
                self.cre_graph.add_edge(f"CRE: {group.id}", f"CRE: {cre.id}")
            else:
                logger.warning(
                    f"A link between CREs {group.external_id} and"
                    f" {cre.external_id} "
                    f"would introduce cycle {cycle}, skipping"
                )

    def add_link(
        self,
        cre: CRE,
        standard: Standard,
        type: cre_defs.LinkTypes = cre_defs.LinkTypes.Same,
    ) -> None:

        if cre.id is None:
            cre = (
                self.session.query(CRE).filter(sqla.and_(CRE.name == cre.name)).first()
            )
        if standard.id is None:
            standard = (
                self.session.query(Standard)
                .filter(
                    sqla.and_(
                        Standard.name == standard.name,
                        Standard.section == standard.section,
                        Standard.subsection == standard.subsection,
                        Standard.version == standard.version,
                    )
                )
                .first()
            )

        entry = (
            self.session.query(Links)
            .filter(sqla.and_(Links.cre == cre.id, Links.standard == standard.id))
            .first()
        )
        if entry:
            logger.debug(
                f"knew of link {standard.name}:{standard.section}"
                f"=={cre.name} of type {entry.type},"
                f"updating type to {type.value}"
            )
            entry.type = type.value
            self.session.commit()
            return
        else:
            cycle = self.__introduces_cycle(
                f"CRE: {cre.id}", f"Standard: {str(standard.id)}"
            )
            if not cycle:
                logger.debug(
                    f"did not know of link {standard.id})"
                    f"{standard.name}:{standard.section}=={cre.id}){cre.name}"
                    " ,adding"
                )
                self.session.add(
                    Links(type=type.value, cre=cre.id, standard=standard.id)
                )
                self.cre_graph.add_edge(
                    f"CRE: {cre.id}", f"Standard: {str(standard.id)}"
                )
            else:
                logger.warning(
                    f"A link between CRE {cre.external_id}"
                    f" and Standard: {standard.name}"
                    f":{standard.section}:{standard.subsection}"
                    f" would introduce cycle {cycle}, skipping"
                )
                logger.debug(f"{cycle}")
        self.session.commit()

    def find_path_between_standards(
        self, standard_source_id: int, standard_destination_id: int
    ) -> bool:
        """One line method to return paths in a graph,
        this starts getting complicated when we have more linktypes"""
        res: bool = nx.has_path(
            self.cre_graph.to_undirected(),
            "Standard: " + str(standard_source_id),
            "Standard: " + str(standard_destination_id),
        )

        return res

    def gap_analysis(self, standards: List[str]) -> List[cre_defs.Standard]:
        """Since the CRE structure is a tree-like graph with
        leaves being standards we can find the paths between standards
        find_path_between_standards() is a graph-path-finding method
        """
        processed_standards = []
        dbstands = []
        for stand in standards:
            dbstands.extend(
                self.session.query(Standard).filter(Standard.name == stand).all()
            )

        for standard in dbstands:
            working_standard = StandardFromDB(standard)
            for other_standard in dbstands:
                if standard.id == other_standard.id:
                    continue
                if self.find_path_between_standards(standard.id, other_standard.id):
                    working_standard.add_link(
                        cre_defs.Link(
                            ltype=cre_defs.LinkTypes.LinkedTo,
                            document=StandardFromDB(other_standard),
                        )
                    )
            processed_standards.append(working_standard)
        return processed_standards

    def text_search(self, text: str) -> List[Optional[cre_defs.Document]]:
        """Given a piece of text, tries to find the best match
        for the text in the database.
        Shortcuts:
           'CRE:<id>' will search for the <id> in cre external ids
           'CRE:<name>' will search for the <name> in cre names
           'Standard:<name>[:<section>:subsection]' will search for
               all entries of <name> and optionally, section/subsection
           '\d\d\d-\d\d\d' (two sets of 3 digits) will first try to match
                CRE ids before it performs a free text search
           Anything else will be a case insensitive LIKE query in the database
        """
        # structured text search first
        cre_id_search = r"CRE(:| )(?P<id>\d+-\d+)"
        cre_naked_id_search = r"\d\d\d-\d\d\d"
        cre_name_search = r"CRE(:| )(?P<name>\w+)"
        standard_search = r"Standard((:| )(?P<link>https?://[^\s]+))?((:| )(?P<val1>\w+))?((:| )(?P<val2>.+))?((:| )(?P<val3>.+))?"
        match = re.search(cre_id_search, text, re.IGNORECASE)
        if match:
            return self.get_CREs(external_id=match.group("id"))

        match = re.search(cre_naked_id_search, text, re.IGNORECASE)
        if match:
            return self.get_CREs(external_id=match.group())

        match = re.search(cre_name_search, text, re.IGNORECASE)
        if match:
            return self.get_CREs(name=match.group("name"))

        match = re.search(standard_search, text, re.IGNORECASE)
        if match:
            link = match.group("link")
            args = [match.group("val1"), match.group("val2"), match.group("val3")]
            results = []
            for combo in permutations(args, 3):
                stands = self.get_standards(
                    name=combo[0], section=combo[1], subsection=combo[2], link=link
                )
                if stands:
                    results.extend(stands)
            return list(set(results))

        # fuzzy matches second
        args = [f"%{text}%", None, None, None]
        results = []
        for combo in permutations(args, 4):
            stands = self.get_standards(
                name=combo[0],
                section=combo[1],
                subsection=combo[2],
                link=combo[3],
                partial=True,
            )
            if stands:
                results.extend(stands)
        args = [f"%{text}%", None, None]
        for combo in permutations(args, 3):
            cres = self.get_CREs(
                name=combo[0], external_id=combo[1], description=combo[2], partial=True
            )
            if cres:
                results.extend(cres)
        return list(set(results))


def dbStandardFromStandard(standard: cre_defs.Standard) -> Standard:
    """Returns a db Standard object dropping the links"""
    return Standard(
        name=standard.name,
        section=standard.section,
        subsection=standard.subsection,
        version=standard.version,
    )


def StandardFromDB(dbstandard: Standard) -> cre_defs.Standard:

    tags = set()
    if dbstandard.tags:
        tags = set(dbstandard.tags.split(","))
    return cre_defs.Standard(
        name=dbstandard.name,
        section=dbstandard.section,
        subsection=dbstandard.subsection,
        hyperlink=dbstandard.link,
        tags=tags,
        version=dbstandard.version,
    )


def CREfromDB(dbcre: CRE) -> cre_defs.CRE:

    tags = set()
    if dbcre.tags:
        tags = set(dbcre.tags.split(","))
    return cre_defs.CRE(
        name=dbcre.name, description=dbcre.description, id=dbcre.external_id, tags=tags
    )
