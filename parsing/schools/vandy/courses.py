# Copyright (C) 2017 Semester.ly Technologies, LLC
#
# Semester.ly is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Semester.ly is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.


from __future__ import absolute_import, division, print_function

import re
import sys

from parsing.library.base_parser import BaseParser
from parsing.library.internal_exceptions import CourseParseError
from parsing.library.utils import safe_cast
from parsing.library.exceptions import ParseError
from semesterly.settings import get_secret


class Parser(BaseParser):
    """Vanderbilt course parser.

    Attributes:
        API_URL (str): Description
        course (TYPE): Description
        CREDENTIALS (TYPE): Description
        departments (dict): Description
        SCHOOL (str): Description
        verbosity (TYPE): Description
    """

    API_URL = 'https://webapp.mis.vanderbilt.edu/more'
    CREDENTIALS = {
        'USERNAME': get_secret('VANDY_USER'),
        'PASSWORD': get_secret('VANDY_PASS')
    }

    def __init__(self, **kwargs):
        """Construct parser instance.

        Args:
            **kwargs: pass-through
        """
        self.departments = {}
        self.course = {
            'description': '',
            'cancelled': False
        }
        super(Parser, self).__init__('vandy', **kwargs)

    def login(self):
        if self.verbosity > 2:
            print("Logging in...")
        login_url = 'https://login.mis.vanderbilt.edu'
        get_login_url = login_url + '/login'
        params = {
            'service': Parser.API_URL + '/j_spring_cas_security_check'
        }
        soup = self.requester.get(get_login_url, params)
        post_suffix_url = soup.find('form', {'name': 'loginForm'})['action']
        sec_block = soup.find('input', {'name': 'lt'})['value']
        login_info = {
            'username': Parser.CREDENTIALS['USERNAME'],
            'password': Parser.CREDENTIALS['PASSWORD'],
            'lt': sec_block,
            '_eventId': 'submit',
            'submit': 'LOGIN'
        }
        self.requester.post(login_url + post_suffix_url,
                            login_info, params,
                            parse=False)
        self.requester.get(Parser.API_URL + '/Entry.action',
                           parse=False)

    def start(self,
              years=None,
              terms=None,
              departments=None,
              textbooks=True,
              verbosity=3):

        self.verbosity = verbosity

        self.login()

        # TODO - read from site and filter based on kwargs
        years_and_terms = {
            '2016': {
                'Fall': '0875'
            },
            '2017': {
                'Spring': '0880',
                'Fall': '0895',
                'Summer': '0885',
            }
        }

        years_and_terms = self.extractor.filter_term_and_year(
            years_and_terms,
            years,
            terms
        )

        for year, semesters in years_and_terms.items():
            if self.verbosity >= 1:
                print('>   Parsing year ' + year)
            self.ingestor['year'] = year

            for semester_name, semester_code in semesters.items():

                if self.verbosity >= 1:
                    print('>>  Parsing semester ' + semester_name)
                self.ingestor['semester'] = semester_name

                # Load environment for targeted semester
                self.requester.get(
                    '{}{}'.format(
                        Parser.API_URL,
                        '/SelectTerm!selectTerm.action'),
                    {'selectedTermCode': semester_code},
                    parse=False)

                self.requester.get(
                    '{}{}'.format(
                        Parser.API_URL,
                        '/SelectTerm!updateSessions.action'),
                    parse=False)

                # Get a list of all the department codes
                department_codes = self.extract_department_codes()
                department_codes = self.extractor.filter_departments(
                    department_codes,
                    departments
                )

                # Create payload to request course list from server
                payload = {
                    'searchCriteria.classStatusCodes': [
                        'O', 'W', 'C'
                    ],
                    '__checkbox_searchCriteria.classStatusCodes': [
                        'O', 'W', 'C'
                    ]
                }

                for department_code in department_codes:

                    if self.verbosity >= 1:
                        print('>>> Parsing courses in',
                              self.departments[department_code])

                    # Construct payload with department code
                    payload.update({
                        'searchCriteria.subjectAreaCodes': department_code
                    })

                    # GET html for department course listings
                    html = self.requester.get(
                        '{}{}'.format(
                            Parser.API_URL,
                            '/SearchClassesExecute!search.action'
                        ),
                        payload
                    )

                    # Parse courses in department
                    self.parse_courses_in_department(html)

                # return to search page for next iteration
                self.requester.get(Parser.API_URL + '/Entry.action',
                                   parse=False)

    def create_course(self):
        self.ingestor['school'] = 'vandy'
        self.ingestor['campus'] = 1
        self.ingestor['code'] = self.course.get('code')
        self.ingestor['name'] = self.course.get('name')
        self.ingestor['description'] = self.course.get('description', '')
        self.ingestor['num_credits'] = safe_cast(self.course.get('Hours'),
                                                 float,
                                                 default=0.)
        self.ingestor['areas'] = filter(
            lambda a: bool(a),
            self.course.get('Attributes', '').split(',')
        )

        self.ingestor['prerequisites'] = self.course.get('Requirement(s)')
        self.ingestor['department_name'] = self.departments.get(
            self.course.get('department')
        )
        self.ingestor['level'] = '0'

        created_course = self.ingestor.ingest_course()
        return created_course

    @staticmethod
    def is_float(f):
        try:
            float(f)
            return True
        except TypeError:
            return False

    def create_section(self, created_course):
        if self.course.get('cancelled'):
            self.course['cancelled'] = False
            return None

        else:
            self.ingestor['section'] = self.course.get('section')
            self.ingestor['instructors'] = self.course.get('Instructor(s)', '')
            self.ingestor['size'] = int(self.course.get('Class Capacity'))
            self.ingestor['enrolment'] = int(self.course.get('Total Enrolled'))

            created_section = self.ingestor.ingest_section(created_course)
            return created_section

    def create_offerings(self, created_section):
        if self.course.get('days'):
            for day in list(self.course.get('days')):
                self.ingestor['day'] = day
                self.ingestor['time_start'] = self.course.get('time_start')
                self.ingestor['time_end'] = self.course.get('time_end')
                self.ingestor['location'] = self.course.get('Location')
                self.ingestor.ingest_meeting(created_section)

    def print_course(self):
        for label in self.course:
            try:
                print(label + "::" + self.course[label] + '::')
            except:
                sys.stderr.write("error: UNICODE ERROR\n")
                print(sys.exc_info()[0])

    def update_current_course(self, label, value):
        try:
            self.course[label] = value.strip()
        except:
            print('label:', label, sys.exc_info()[0])
            sys.stderr.write("UNICODE ERROR\n")

    def extract_department_codes(self):

        # Query Vandy class search website
        soup = self.requester.get(
            Parser.API_URL + '/SearchClasses!input.action',
            parse=True)

        # Retrieve all deparments from dropdown in advanced search
        department_entries = soup.find_all(
            id=re.compile("subjAreaMultiSelectOption[0-9]"))

        # Extract department codes from parsed department entries
        department_codes = [de['value'] for de in department_entries]

        for de in department_entries:
            self.departments[de['value']] = de['title']

        return department_codes

    def parse_courses_in_department(self, html):

        # Check number of results isn't over max
        num_hits_search = re.search("totalRecords: ([0-9]*),", str(html))

        num_hits = 0
        if num_hits_search is not None:
            num_hits = int(num_hits_search.group(1))

        # perform more targeted searches if needed
        if num_hits == 300:
            raise CourseParseError('vandy num_hits greater than 300')
        else:
            self.parse_set_of_courses(html)

    def parse_set_of_courses(self, html):

        prev_course_number = 0
        page_count = 1

        while True:
            # Parse page by page
            last_class_number = self.parse_page_of_courses(html)

            # Condition met when reached last page
            if last_class_number == prev_course_number:
                break

            page_count = page_count + 1
            next_page_url = '{}{}{}'.format(
                Parser.API_URL,
                '/SearchClassesExecute!switchPage.action?pageNum=',
                page_count)
            html = self.requester.get(next_page_url)
            prev_course_number = last_class_number

    def parse_page_of_courses(self, html):

        # initial parse with Beautiful Soup
        courses = html.find_all('tr', {'class': 'classRow'})

        last_class_number = 0
        for course in courses:

            # remove cancelled classes
            if course.find('a', {'class': 'cancelledStatus'}):
                self.course['cancelled'] = True

            last_class_number = self.parse_course(course)

        return last_class_number

    def parse_course(self, soup):

        # Extract course code and term number to generate access to more info
        details = soup.find('td', {'class', 'classSection'})['onclick']

        # Extract course number and term code
        search = re.search("showClassDetailPanel.fire\({classNumber : '([0-9]*)', termCode : '([0-9]*)',", details)

        course_number, term_code = search.group(1), search.group(2)

        # Base URL to retrieve detailed course info
        course_details_url = Parser.API_URL \
            + '/GetClassSectionDetail.action'

        # Create payload to request course from server
        payload = {
            'classNumber': course_number,
            'termCode': term_code
        }

        try:
            self.parse_course_details(self.requester.get(course_details_url,
                                                         payload))

            # Create models
            created_section = self.create_section(self.create_course())
            if created_section:
                self.create_offerings(created_section)

            # Clear course map for next pass
            self.course.clear()

        except ParseError:
            print('invalid course, parse exception')

        return course_number

    def parse_course_details(self, html):
        # Extract course name and abbreviation details
        search = re.search(
            "(.*):.*\n(.*)",
            html.find(id='classSectionDetailDialog').find('h1').text)
        courseName, abbr = search.group(2), search.group(1)

        # Extract department code, catalog ID, and section number from abbr
        title = re.match("(\S*)-(\S*)-(\S*)", abbr)

        if not title:
            raise ParseError()

        department_code = title.group(1)
        catalog_id = title.group(2)
        section_number = title.group(3)

        if self.verbosity > 2:
            print('\t-', department_code, catalog_id,
                  section_number.strip(), '-')

        self.update_current_course("name", courseName)
        self.update_current_course("code", department_code + '-' + catalog_id)
        self.update_current_course("department", department_code)
        self.update_current_course("Catalog ID", catalog_id)
        self.update_current_course('section',
                                   '(' + section_number.strip() + ')')

        # in case no description for course
        self.update_current_course('description', '')

        # Deal with course details as subgroups seen on details page
        detail_headers = html.find_all('div', {'class': 'detailHeader'})
        detail_panels = html.find_all('div', {'class': 'detailPanel'})

        # NOTE: there should be equal detail headers and detail panels
        assert(len(detail_headers) == len(detail_panels))

        for i in range(len(detail_headers)):

            # Extract header name
            header = detail_headers[i].text.strip()

            # Choose parsing strategy dependent on header
            if header == "Details" or header == "Availability":
                self.parse_labeled_table(detail_panels[i])

            elif header == "Description":
                self.parse_description(detail_panels[i])

            elif header == "Notes":
                self.parse_notes(detail_panels[i])

            elif header == "Meeting Times":
                self.parse_meeting_times(detail_panels[i])

            elif header == "Cross Listings":
                pass

            elif header == "Attributes":
                self.parse_attributes(detail_panels[i])

            elif header == "Ad Hoc Meeting Times":
                pass

    def parse_attributes(self, soup):

        labels = [l.text.strip() for l in soup.find_all('div', {'class': 'listItem'})]
        self.update_current_course("Attributes", ', '.join(labels))

    def parse_labeled_table(self, soup):

        # Gather all labeled table entries
        labels = soup.find_all('td', {'class' : 'label'})

        for label in labels:

            siblings = label.find_next_siblings()

            # Check if label value exists
            if len(siblings) != 0:

                # Extract pure label from html
                key = label.text[:-1].strip()

                # Extract label's value(s) [deals with multiline multi-values]
                values = [l for l in (line.strip() for line in siblings[0].text.splitlines()) if l]

                # Edge cases
                if key == "Books":
                    # bookURL = re.search("new YAHOO.mis.student.PopUpOpener\('(.*)',", values[0])
                    # values = [bookURL.group(1)]
                    values = ["<long bn url>"]

                elif key == "Hours":
                    values[0] = str(safe_cast(values[0], float, default=0.))

                self.update_current_course(key, ', '.join(values))

    def parse_meeting_times(self, soup):

        # Gather all labeled table entries
        labels = soup.find_all('th', {'class': 'label'})

        values = []
        if len(labels) > 0:
            values = soup.find('tr', {'class': 'courseHeader'}).find_next_siblings()[0].find_all('td')
        else:

            # Create empty times slots
            self.update_current_course('days', '')
            self.update_current_course('time_start', '')
            self.update_current_course('time_end', '')

        # NOTE: number of labels and values should be the same
        assert(len(labels) == len(values))

        for i in range(len(labels)):
            label = labels[i].text.strip()
            value = values[i].text.strip()
            if len(label) > 0 and len(value) > 0:

                if label == "Instructor(s)":
                    self.update_current_course(label, ', '.join(self.extract_instructors(value)))

                elif label == "Time":
                    self.parse_time_range(value)

                elif label == "Days":
                    self.parse_days(value)

                else:
                    self.update_current_course(label, value)

    def parse_days(self, unformatted_days):
        if unformatted_days == "TBA" or unformatted_days == "":
            self.update_current_course("days", "")
        else:
            self.update_current_course("days", unformatted_days)

    def parse_time_range(self, unformatted_time_range):

        if unformatted_time_range == "TBA" or unformatted_time_range == "":

            # Create empty time slots
            self.update_current_course('days', '')
            self.update_current_course('time_start', '')
            self.update_current_course('time_end', '')

        else:

            search = re.match("(.*) \- (.*)", unformatted_time_range)
            if search is not None:
                self.update_current_course('time_start', self.extractor.time_12to24(search.group(1)))
                self.update_current_course('time_end', self.extractor.time_12to24(search.group(2)))
            else:
                print('ERROR: invalid time format', file=sys.stderr)

    def extract_instructors(self, string):

        instructors = string.splitlines()

        for i in range(len(instructors)):

            # Deal with instance of primary instructor
            search = re.match("(.*) \(Primary\)", instructors[i])
            if search is not None:
                instructors[i] = search.group(1)

        return instructors

    def parse_notes(self, soup):
        notes = ' '.join([l for l in (p.strip() for p in soup.text.splitlines()) if l]).strip()
        self.update_current_course('description', self.course.get('description') + '\nNotes: ' + notes)

    def parse_description(self, soup):
        self.update_current_course('description', soup.text.strip())
