'''
Created on Jul 7, 2018

@author: Mark V Systems Limited
(c) Copyright 2018 Mark V Systems Limited, All rights reserved.
'''
import os, json, re
from collections import defaultdict, OrderedDict
from arelle.FileSource import openFileStream, openFileSource, saveFile # only needed if building a cached file
from arelle.ModelValue import qname
from arelle import XbrlConst
from arelle.PythonUtil import attrdict, flattenSequence, pyObjectSize
from .Consts import standardNamespacesPattern, latestTaxonomyDocs, latestDqcrtDocs

EMPTY_DICT = {}

def conflictClassFromNamespace(namespaceURI):
    match = standardNamespacesPattern.match(namespaceURI or "")
    if match:
        _class = match.group(2) or match.group(5)[:4] # trim ifrs-full to ifrs
        if _class.startswith("ifrs"):
            _class = "ifrs"
        return "{}/{}".format(_class, match.group(3) or match.group(4))
        
def abbreviatedNamespace(namespaceURI):
    match = standardNamespacesPattern.match(namespaceURI or "")
    if match:
        return "{}/{}".format(match.group(2) or match.group(5), match.group(3) or match.group(4))
    
def abbreviatedWildNamespace(namespaceURI):
    match = standardNamespacesPattern.match(namespaceURI or "")
    if match:
        return "{}/*".format(match.group(2) or match.group(5))
    return None
    
def loadNonNegativeFacts(modelXbrl):
    signwarnings = loadDqc0015signwarningRules(modelXbrl)
    concepts = set()
    excludedMembers = set()
    excludedMemberStrings = set()
    excludedAxesMembers = defaultdict(set)
    for modelDocument in modelXbrl.urlDocs.values():
        ns = modelDocument.targetNamespace # set up non neg lookup by full NS
        for abbrNs in (abbreviatedNamespace(ns), abbreviatedWildNamespace(ns)):
            nsMatch = False
            for exName, exSet, isQName in (("conceptNames", concepts, True),
                                           ("excludedMemberNames", excludedMembers, True),
                                           ("excludedMemberStrings", excludedMemberStrings, False)):
                for localName in signwarnings[exName].get(abbrNs, ()):
                    exSet.add(qname(ns, localName) if isQName else localName)
                    nsMatch = True
            for localDimName, localMemNames in signwarnings["excludedAxesMembers"].get(abbrNs, EMPTY_DICT).items():
                for localMemName in localMemNames:
                    excludedAxesMembers[qname(ns, localDimName)].add(qname(ns, localMemName) if localMemName != "*" else "*")
                    nsMatch = True
            if nsMatch:
                break # use explicit year rules if available, else generic year rules
    return attrdict(concepts=concepts, 
                    excludedAxesMembers=excludedAxesMembers, 
                    excludedMembers=excludedMembers, 
                    excludedMemberNamesPattern=re.compile("|".join(excludedMemberStrings), re.IGNORECASE) 
                                               if excludedMemberStrings else None)
    
def loadCustomAxesReplacements(modelXbrl): # returns match expression, standard patterns
    _file = openFileStream(modelXbrl.modelManager.cntlr, resourcesFilePath(modelXbrl.modelManager, "axiswarnings.json"), 'rt', encoding='utf-8')
    axiswarnings = json.load(_file) # {localName: date, ...}
    _file.close()
    standardAxes = {}
    matchPattern = []
    for i, (standardAxis, customAxisPattern) in enumerate(axiswarnings.items()):
        if standardAxis not in ("#", "copyright", "description"):
            patternName = "_{}".format(i)
            standardAxes[patternName] = standardAxis
            matchPattern.append("(?P<{}>^{}$)".format(patternName, customAxisPattern))
    return attrdict(standardAxes=standardAxes, 
                    customNamePatterns=re.compile("|".join(matchPattern)))

def loadDeiValidations(modelXbrl, isInlineXbrl):
    _file = openFileStream(modelXbrl.modelManager.cntlr, resourcesFilePath(modelXbrl.modelManager, "dei-validations.json"), 'rt', encoding='utf-8')
    validations = json.load(_file) # {localName: date, ...}
    _file.close()
    #print ("original validations size {}".format(pyObjectSize(validations)))
    # get dei namespaceURI
    deiNamespaceURI = None
    for doc in modelXbrl.urlDocs.values():
         if doc.targetNamespace and doc.targetNamespace.startswith("http://xbrl.sec.gov/dei/"):
             deiNamespaceURI = doc.targetNamespace
             break
    # compile form-classes
    fc = validations["form-classes"]
    def compileFormSet(forms, formSet=None, visitedClasses=None):
        if visitedClasses is None: visitedClasses = set()
        if formSet is None: formSet = set()
        for form in flattenSequence(forms):
            if form.startswith("@"):
                referencedClass = form[1:]
                if referencedClass not in fc:
                    modelXbrl.error("arelle:loadDeiValidations", _("Missing declaration for %(referencedClass)s."), referencedClass=form)
                elif form in visitedClasses:
                    modelXbrl.error("arelle:loadDeiValidations", 
                                    _("Circular reference to %(formClass)s in %(formClasses)s."),
                                    formClass=referencedClass, formClasses=sorted(visitedClasses))
                else:
                    visitedClasses.add(form)
                    compileFormSet(fc[referencedClass], formSet, visitedClasses)
            else:
                formSet.add(form)
        return formSet
    for fev in validations["form-element-validations"]:
        for field in (
            ("xbrl-names",) if "store-db-name" in fev else
            ("xbrl-names", "validation", "efm", "source")):
            if field not in fev:
                modelXbrl.error("arelle:loadDeiValidations", 
                                _("Missing form-element-validation[\"%(field)s\"] from %(validation)s."), 
                                field=field, validation=fev)
        if "severity" in fev and not any(field.startswith("message") for field in fev):
            modelXbrl.error("arelle:loadDeiValidations", 
                            _("Missing form-element-validation[\"%(field)s\"] from %(validation)s."), 
                            field="message*", validation=fev)
        validationCode = fev.get("validation")
        if validationCode in ("f2", "og", "ol1", "ol2", "oph", "ar", "sr", "oth", "t", "tb", "t1", "te") and "references" not in fev:
            modelXbrl.error("arelle:loadDeiValidations", 
                            _("Missing form-element-validation[\"references\"] from %(validation)s."), 
                            field=field, validation=fev)
        if validationCode in ("ru", "ou"):
            if isinstance(fev.get("value"), list):
                fev["value"] = set(fev["value"]) # change options list into set
            else:
                modelXbrl.error("arelle:loadDeiValidations", 
                                _("Missing form-element-validation[\"value\"] from %(validation)s, must be a list."), 
                                field=field, validation=fev)
        if validationCode in ():
            if isinstance(fev.get("reference-value"), list):
                fev["reference-value"] = set(fev["reference-value"]) # change options list into set
            else:
                modelXbrl.error("arelle:loadDeiValidations", 
                                _("Missing form-element-validation[\"value\"] from %(validation)s, must be a list."), 
                                field=field, validation=fev)
        if not validationCode and "store-db-name" in fev:
            fev["validation"] = None # only storing, no validation
        elif validationCode not in validations["validations"]:
            modelXbrl.error("arelle:loadDeiValidations", _("Missing validation[\"%(validationCode)s\"]."), validationCode=validationCode)
        axisCode = fev.get("axis")
        if axisCode and axisCode not in validations["axis-validations"]:
            modelXbrl.error("arelle:loadDeiValidations", _("Missing axis[\"%(axisCode)s\"]."), axisCode=axisCode)
        if "lang" in fev:
            fev["langPattern"] = re.compile(fev["lang"])
        s = fev.get("source")
        if s is None and not validationCode and "store-db-name" in fev:
            pass # not a validation entry
        elif s not in ("inline", "non-inline", "both"):
            modelXbrl.error("arelle:loadDeiValidations", _("Invalid source [\"%(source)s\"]."), source=s)
        elif (isInlineXbrl and s in ("inline", "both")) or (not isInlineXbrl and s in ("non-inline", "both")):
            messageKey = fev.get("message")
            if messageKey and messageKey not in validations["messages"]:
                modelXbrl.error("arelle:loadDeiValidations", _("Missing message[\"%(messageKey)s\"]."), messageKey=messageKey)
            # only include dei names in current dei taxonomy
            fev["xbrl-names"] = [name
                                 for name in flattenSequence(fev.get("xbrl-names", ()))
                                 if qname(deiNamespaceURI, name) in modelXbrl.qnameConcepts]
            formSet = compileFormSet(fev.get("forms", (fev.get("form",()),)))
            if "*" in formSet:
                formSet = "all" # change to string for faster testing in Filing.py
            fev["formSet"] = formSet
        
    for axisKey, axisValidation in validations["axis-validations"].items():
        messageKey = axisValidation.get("message")
        if messageKey and messageKey not in validations["messages"]:
            modelXbrl.error("arelle:loadDeiValidations", _("Missing axis \"%(axisKey)s\" message[\"%(messageKey)s\"]."), 
                            axisKey=axisKey, messageKey=messageKey)
    for valKey, validation in validations["validations"].items():
        messageKey = validation.get("message")
        if messageKey and messageKey not in validations["messages"]:
            modelXbrl.error("arelle:loadDeiValidations", _("Missing validation \"%(valKey)s\" message[\"%(messageKey)s\"]."), 
                            valKey=valKey, messageKey=messageKey)
        
#print ("compiled validations size {}".format(pyObjectSize(validations)))
    return validations

def loadDeprecatedConceptDates(val, deprecatedConceptDates):  
    for modelDocument in val.modelXbrl.urlDocs.values():
        ns = modelDocument.targetNamespace
        abbrNs = abbreviatedWildNamespace(ns)
        if abbrNs in latestTaxonomyDocs:
            latestTaxonomyDoc = latestTaxonomyDocs[abbrNs]
            _fileName = deprecatedConceptDatesFile(val.modelXbrl.modelManager, abbrNs, latestTaxonomyDoc)
            if _fileName:
                _file = openFileStream(val.modelXbrl.modelManager.cntlr, _fileName, 'rt', encoding='utf-8')
                _deprecatedConceptDates = json.load(_file) # {localName: date, ...}
                _file.close()
                for localName, date in _deprecatedConceptDates.items():
                    deprecatedConceptDates[qname(ns, localName)] = date
                
def resourcesFilePath(modelManager, fileName):
    # resourcesDir can be in cache dir (production) or in validate/EFM/resources (for development)
    _resourcesDir = os.path.join( os.path.dirname(__file__), "resources") # dev/testing location
    _target = "validate/EFM/resources"
    if not os.path.isabs(_resourcesDir):
        _resourcesDir = os.path.abspath(_resourcesDir)
    if not os.path.exists(_resourcesDir): # production location
        _resourcesDir = os.path.join(modelManager.cntlr.webCache.cacheDir, "resources", "validation", "EFM")
        _target = "web-cache/resources"
    return os.path.join(_resourcesDir, fileName)
                    
def deprecatedConceptDatesFile(modelManager, abbrNs, latestTaxonomyDoc):
    cntlr = modelManager.cntlr
    _fileName = resourcesFilePath(modelManager, abbrNs.partition("/")[0] + "-deprecated-concepts.json")
    _deprecatedLabelRole = latestTaxonomyDoc["deprecatedLabelRole"]
    _deprecatedDateMatchPattern = latestTaxonomyDoc["deprecationDatePattern"]
    if os.path.exists(_fileName):
        return _fileName
    # load labels and store file name
    modelManager.addToLog(_("loading {} deprecated concepts into {}").format(abbrNs, _fileName), messageCode="info")
    deprecatedConceptDates = {}
    # load without SEC/EFM validation (doc file would not be acceptable)
    priorValidateDisclosureSystem = modelManager.validateDisclosureSystem
    modelManager.validateDisclosureSystem = False
    from arelle import ModelXbrl
    for latestTaxonomyLabelFile in flattenSequence(latestTaxonomyDoc["deprecatedLabels"]):
        deprecationsInstance = ModelXbrl.load(modelManager, 
              # "http://xbrl.fasb.org/us-gaap/2012/elts/us-gaap-doc-2012-01-31.xml",
              # load from zip (especially after caching) is incredibly faster
              openFileSource(latestTaxonomyLabelFile, cntlr), 
              _("built deprecations table in cache"))
        modelManager.validateDisclosureSystem = priorValidateDisclosureSystem
        if deprecationsInstance is None:
            modelManager.addToLog(
                _("%(name)s documentation not loaded"),
                messageCode="arelle:notLoaded", messageArgs={"modelXbrl": val, "name":_abbrNs})
        else:   
            # load deprecations
            for labelRel in deprecationsInstance.relationshipSet(XbrlConst.conceptLabel).modelRelationships:
                modelLabel = labelRel.toModelObject
                conceptName = labelRel.fromModelObject.name
                if modelLabel.role == _deprecatedLabelRole:
                    match = _deprecatedDateMatchPattern.match(modelLabel.text)
                    if match is not None:
                        date = match.group(1)
                        if date:
                            deprecatedConceptDates[conceptName] = date
            jsonStr = _STR_UNICODE(json.dumps(deprecatedConceptDates, ensure_ascii=False, indent=0)) # might not be unicode in 2.7
            saveFile(cntlr, _fileName, jsonStr)  # 2.7 gets unicode this way
            deprecationsInstance.close()
            del deprecationsInstance # dereference closed modelXbrl
                    
def loadDqc0015signwarningRules(modelXbrl):
    conceptRule = "http://fasb.org/dqcrules/arcrole/concept-rule" # FASB arcrule
    rule0015 = "http://fasb.org/us-gaap/role/dqc/0015"
    modelManager = modelXbrl.modelManager
    cntlr = modelXbrl.modelManager.cntlr
    # check for cached completed signwarnings
    _signwarningsFileName = resourcesFilePath(modelManager, "signwarnings.json")
    if os.path.exists(_signwarningsFileName): 
        _file = openFileStream(modelManager.cntlr, _signwarningsFileName, 'rt', encoding='utf-8')
        signwarnings = json.load(_file) # {localName: date, ...}
        _file.close()
        return signwarnings
    # load template rules
    _fileName = resourcesFilePath(modelManager, "signwarnings-template.json")
    if _fileName:
        _file = openFileStream(modelXbrl.modelManager.cntlr, _fileName, 'rt', encoding='utf-8')
        signwarnings = json.load(_file, object_pairs_hook=OrderedDict) # {localName: date, ...}
        _file.close()

    # load rules and add to signwarnings template
    for dqcAbbr, dqcrtUrl in latestDqcrtDocs.items():
        modelManager.addToLog(_("loading {} DQC Rules {}").format(dqcAbbr, dqcrtUrl), messageCode="info")
        # load without SEC/EFM validation (doc file would not be acceptable)
        priorValidateDisclosureSystem = modelManager.validateDisclosureSystem
        modelManager.validateDisclosureSystem = False
        from arelle import ModelXbrl
        dqcrtInstance = ModelXbrl.load(modelManager, 
              # "http://xbrl.fasb.org/us-gaap/2012/elts/us-gaap-doc-2012-01-31.xml",
              # load from zip (especially after caching) is incredibly faster
              openFileSource(dqcrtUrl, cntlr), 
              _("built dqcrt table in cache"))
        modelManager.validateDisclosureSystem = priorValidateDisclosureSystem
        if dqcrtInstance is None:
            modelManager.addToLog(
                _("%(name)s documentation not loaded"),
                messageCode="arelle:notLoaded", messageArgs={"modelXbrl": val, "name":dqcAbbr})
        else:   
            # load signwarnings from DQC 0015
            dqcRelSet = dqcrtInstance.relationshipSet(conceptRule, rule0015)
            for signWrnObj, headEltName in (("conceptNames", "Dqc_0015_ListOfElements"),
                                            ("excludedMemberNames", "Dqc_0015_ExcludeNonNegMembersAbstract"),
                                            ("excludedAxesMembers", "Dqc_0015_ExcludeNonNegAxisAbstract"),
                                            ("excludedAxesMembers", "Dqc_0015_ExcludeNonNegAxisMembersAbstract"),
                                            ("excludedMemberStrings", "Dqc_0015_ExcludeNonNegMemberStringsAbstract")):
                headElts = dqcrtInstance.nameConcepts.get(headEltName,())
                for headElt in headElts:
                    if signWrnObj == "excludedMemberStrings":
                        for refRel in dqcrtInstance.relationshipSet(XbrlConst.conceptReference).fromModelObject(headElt):
                            for refPart in refRel.toModelObject.iterchildren("{*}allowableSubString"):
                                for subStr in refPart.text.split():
                                    signwarnings[signWrnObj].setdefault(nsAbbr, []).append(subStr)
                    else:
                        for ruleRel in dqcRelSet.fromModelObject(headElt):
                            elt = ruleRel.toModelObject
                            nsAbbr = abbreviatedNamespace(elt.qname.namespaceURI)
                            if signWrnObj in ("conceptNames", "excludedMemberNames"):
                                signwarnings[signWrnObj].setdefault(nsAbbr, []).append(elt.name)
                            else:
                                l = signwarnings[signWrnObj].setdefault(nsAbbr, {}).setdefault(elt.name, [])
                                if headEltName == "Dqc_0015_ExcludeNonNegAxisAbstract":
                                    l.append("*")
                                else:
                                    for memRel in dqcRelSet.fromModelObject(elt):
                                        l.append(memRel.toModelObject.name)
            jsonStr = _STR_UNICODE(json.dumps(signwarnings, ensure_ascii=False, indent=2)) # might not be unicode in 2.7
            saveFile(cntlr, _signwarningsFileName, jsonStr)  # 2.7 gets unicode this way
            dqcrtInstance.close()
            del dqcrtInstance # dereference closed modelXbrl
    return signwarnings
    
def buildDeprecatedConceptDatesFiles(cntlr):
    # will build in subdirectory "resources" if exists, otherwise in cache/resources
    for abbrNs, latestTaxonomyDoc in latestTaxonomyDocs.items():
        if latestTaxonomyDoc is not None and abbrNs and abbrNs != "invest/*":
            # don't rebuild invest, use static file of all entries
            deprecatedConceptDatesFile(cntlr.modelManager, abbrNs, latestTaxonomyDoc)
        
def loadOtherStandardTaxonomies(modelXbrl, val):
    _file = openFileStream(modelXbrl.modelManager.cntlr, resourcesFilePath(modelXbrl.modelManager, "other-standard-taxonomies.json"), 'rt', encoding='utf-8')
    otherStandardTaxonomies = json.load(_file) # {localName: date, ...}
    _file.close()
    otherStandardNsPrefixes = otherStandardTaxonomies.get("taxonomyPrefixes",{})
    return set(doc.targetNamespace
               for doc in modelXbrl.urlDocs.values()
               if doc.targetNamespace and 
               doc.targetNamespace not in val.disclosureSystem.standardTaxonomiesDict
               and any(doc.targetNamespace.startswith(nsPrefix) for nsPrefix in otherStandardNsPrefixes))
