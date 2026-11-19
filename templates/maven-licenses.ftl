<#list dependencyMap as e><#assign p = e.getKey()/>${p.groupId}:${p.artifactId}	${p.version}	<#list e.getValue() as l>${l}<#sep> | </#sep></#list>
</#list>
