from Athena.AtCore import Tag, Link, ID, Status

header = \
(
	ID.TestCheck,
)

register = \
{

	ID.TestCheck:
		{
			'process': 'Athena.ressources.Athena_example.Ajna_Test.blender.processes.testCheck.TestCheck',
			'category': 'Sanity Test',
		}, 

}

parameters = \
{
	'recheck': True
}
